"""aiosqlite-backed state store (spec §6: state.db = «один writer»).

Tables (S1 MVP, расширяется в S2-S8):
- agent_session   — записи каждой orchestrator run (CLI start → stop)
- budget_tracker  — текущий spend по scope (story/batch/wave/day/phase)
- event_queue    — append-only event log для replay + audit

Контракт single-writer: все INSERT/UPDATE/DELETE идут через async functions
этого модуля. SELECT'ы можно из любого потока.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

SCHEMA_SQL: str = """
CREATE TABLE IF NOT EXISTS agent_session (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    target_project  TEXT    NOT NULL,
    wave            TEXT    NOT NULL,
    max_parallel    INTEGER NOT NULL DEFAULT 2,
    status          TEXT    NOT NULL DEFAULT 'running'
                    CHECK (status IN ('running', 'paused', 'stopped', 'error')),
    started_at      TEXT    NOT NULL,
    ended_at        TEXT,
    notes           TEXT
);

CREATE INDEX IF NOT EXISTS idx_agent_session_status ON agent_session(status);

CREATE TABLE IF NOT EXISTS budget_tracker (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id        INTEGER NOT NULL REFERENCES agent_session(id) ON DELETE CASCADE,
    scope             TEXT    NOT NULL
                      CHECK (scope IN ('story', 'batch', 'wave', 'day', 'phase')),
    scope_target_id   TEXT    NOT NULL,
    spent_usd         REAL    NOT NULL DEFAULT 0.0,
    spent_tokens      INTEGER NOT NULL DEFAULT 0,
    alarm_threshold   REAL    NOT NULL,
    halt_threshold    REAL    NOT NULL,
    breached_alarm    INTEGER NOT NULL DEFAULT 0,
    breached_halt     INTEGER NOT NULL DEFAULT 0,
    updated_at        TEXT    NOT NULL,
    UNIQUE (session_id, scope, scope_target_id)
);

CREATE INDEX IF NOT EXISTS idx_budget_session_scope
    ON budget_tracker(session_id, scope);

CREATE TABLE IF NOT EXISTS event_queue (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    INTEGER NOT NULL REFERENCES agent_session(id) ON DELETE CASCADE,
    event_type    TEXT    NOT NULL,
    payload_json  TEXT    NOT NULL DEFAULT '{}',
    emitted_at    TEXT    NOT NULL,
    consumed_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_event_queue_unconsumed
    ON event_queue(session_id, consumed_at)
    WHERE consumed_at IS NULL;
"""


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


async def init_db(db_path: Path) -> None:
    """Create tables if not exist. Idempotent."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(db_path)) as conn:
        await conn.executescript(SCHEMA_SQL)
        await conn.commit()


@asynccontextmanager
async def connect(db_path: Path) -> AsyncIterator[aiosqlite.Connection]:
    """Async context manager — открывает connection с включёнными foreign keys."""
    async with aiosqlite.connect(str(db_path)) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys = ON")
        yield conn


@dataclass(slots=True)
class StateDB:
    """High-level CRUD façade. Все write-ops auto-commit."""

    db_path: Path

    async def init(self) -> None:
        await init_db(self.db_path)

    async def create_session(
        self,
        target_project: str,
        wave: str,
        max_parallel: int = 2,
    ) -> int:
        async with connect(self.db_path) as conn:
            cur = await conn.execute(
                """
                INSERT INTO agent_session
                  (target_project, wave, max_parallel, status, started_at)
                VALUES (?, ?, ?, 'running', ?)
                """,
                (target_project, wave, max_parallel, _utc_now()),
            )
            await conn.commit()
            assert cur.lastrowid is not None
            return cur.lastrowid

    async def end_session(self, session_id: int, status: str = "stopped") -> None:
        async with connect(self.db_path) as conn:
            await conn.execute(
                """
                UPDATE agent_session
                   SET status = ?, ended_at = ?
                 WHERE id = ?
                """,
                (status, _utc_now(), session_id),
            )
            await conn.commit()

    async def upsert_budget(
        self,
        session_id: int,
        scope: str,
        scope_target_id: str,
        spent_usd: float,
        spent_tokens: int,
        alarm_threshold: float,
        halt_threshold: float,
    ) -> None:
        breached_alarm = 1 if spent_usd >= alarm_threshold else 0
        breached_halt = 1 if spent_usd >= halt_threshold else 0
        async with connect(self.db_path) as conn:
            await conn.execute(
                """
                INSERT INTO budget_tracker
                  (session_id, scope, scope_target_id,
                   spent_usd, spent_tokens,
                   alarm_threshold, halt_threshold,
                   breached_alarm, breached_halt, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, scope, scope_target_id) DO UPDATE SET
                  spent_usd       = excluded.spent_usd,
                  spent_tokens    = excluded.spent_tokens,
                  alarm_threshold = excluded.alarm_threshold,
                  halt_threshold  = excluded.halt_threshold,
                  breached_alarm  = excluded.breached_alarm,
                  breached_halt   = excluded.breached_halt,
                  updated_at      = excluded.updated_at
                """,
                (
                    session_id,
                    scope,
                    scope_target_id,
                    spent_usd,
                    spent_tokens,
                    alarm_threshold,
                    halt_threshold,
                    breached_alarm,
                    breached_halt,
                    _utc_now(),
                ),
            )
            await conn.commit()

    async def enqueue_event(
        self,
        session_id: int,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> int:
        async with connect(self.db_path) as conn:
            cur = await conn.execute(
                """
                INSERT INTO event_queue
                  (session_id, event_type, payload_json, emitted_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    session_id,
                    event_type,
                    json.dumps(payload or {}, ensure_ascii=False),
                    _utc_now(),
                ),
            )
            await conn.commit()
            assert cur.lastrowid is not None
            return cur.lastrowid

    async def claim_next_event(self, session_id: int) -> dict[str, Any] | None:
        """Mark oldest unconsumed event as consumed, return its dict.

        Single-writer guarantee — race-free под предположением одного
        orchestrator-процесса на DB (см. spec §6).
        """
        async with connect(self.db_path) as conn:
            cur = await conn.execute(
                """
                SELECT id, event_type, payload_json, emitted_at
                  FROM event_queue
                 WHERE session_id = ? AND consumed_at IS NULL
                 ORDER BY id ASC
                 LIMIT 1
                """,
                (session_id,),
            )
            row = await cur.fetchone()
            if row is None:
                return None
            await conn.execute(
                "UPDATE event_queue SET consumed_at = ? WHERE id = ?",
                (_utc_now(), row["id"]),
            )
            await conn.commit()
            return {
                "id": row["id"],
                "event_type": row["event_type"],
                "payload": json.loads(row["payload_json"]),
                "emitted_at": row["emitted_at"],
            }
