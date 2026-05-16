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
        """Additive upsert: spent_usd/spent_tokens ACCUMULATE on conflict.

        Wrapped in BEGIN IMMEDIATE so concurrent writers serialise on the
        write lock — race-free for B5 budget aggregation. The breached_*
        flags are recomputed from the post-update total inside the same
        transaction (sub-select on excluded + existing).
        """
        from bmad_orchestrator.runtime.budget import is_finite_spend

        if not is_finite_spend(spent_usd):
            raise ValueError(
                f"spent_usd must be finite and non-negative, got {spent_usd!r}"
            )
        if not isinstance(spent_tokens, int) or isinstance(spent_tokens, bool):
            raise TypeError("spent_tokens must be int")
        if spent_tokens < 0:
            raise ValueError(f"spent_tokens must be non-negative, got {spent_tokens}")

        async with connect(self.db_path) as conn:
            await conn.execute("BEGIN IMMEDIATE")
            try:
                await conn.execute(
                    """
                    INSERT INTO budget_tracker
                      (session_id, scope, scope_target_id,
                       spent_usd, spent_tokens,
                       alarm_threshold, halt_threshold,
                       breached_alarm, breached_halt, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(session_id, scope, scope_target_id) DO UPDATE SET
                      spent_usd       = budget_tracker.spent_usd + excluded.spent_usd,
                      spent_tokens    = budget_tracker.spent_tokens + excluded.spent_tokens,
                      alarm_threshold = excluded.alarm_threshold,
                      halt_threshold  = excluded.halt_threshold,
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
                        1 if spent_usd >= alarm_threshold else 0,
                        1 if spent_usd >= halt_threshold else 0,
                        _utc_now(),
                    ),
                )
                await conn.execute(
                    """
                    UPDATE budget_tracker
                       SET breached_alarm = CASE
                               WHEN spent_usd >= alarm_threshold THEN 1 ELSE 0 END,
                           breached_halt  = CASE
                               WHEN spent_usd >= halt_threshold  THEN 1 ELSE 0 END
                     WHERE session_id = ?
                       AND scope = ?
                       AND scope_target_id = ?
                    """,
                    (session_id, scope, scope_target_id),
                )
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise

    async def get_budget(
        self,
        session_id: int,
        scope: str,
        scope_target_id: str,
    ) -> dict[str, Any] | None:
        """Snapshot read of one budget row (None if absent)."""
        async with connect(self.db_path) as conn:
            cur = await conn.execute(
                """
                SELECT spent_usd, spent_tokens, alarm_threshold, halt_threshold,
                       breached_alarm, breached_halt, updated_at
                  FROM budget_tracker
                 WHERE session_id = ? AND scope = ? AND scope_target_id = ?
                """,
                (session_id, scope, scope_target_id),
            )
            row = await cur.fetchone()
            if row is None:
                return None
            return {
                "spent_usd": row["spent_usd"],
                "spent_tokens": row["spent_tokens"],
                "alarm_threshold": row["alarm_threshold"],
                "halt_threshold": row["halt_threshold"],
                "breached_alarm": bool(row["breached_alarm"]),
                "breached_halt": bool(row["breached_halt"]),
                "updated_at": row["updated_at"],
            }

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

    async def enqueue_human_query(
        self,
        session_id: int,
        chat_id: int,
        text: str,
        corr_id: str,
    ) -> int:
        """FS4 B9 cross-process bridge: bot → agent message envelope.

        Stored as ``event_queue`` row with ``event_type='human_query'`` and
        payload carrying ``{chat_id, corr_id, text}``. Agent process polls
        ``claim_next_event`` and matches by ``corr_id``.
        """
        return await self.enqueue_event(
            session_id,
            "human_query",
            {"chat_id": chat_id, "corr_id": corr_id, "text": text},
        )

    async def enqueue_human_response(
        self,
        session_id: int,
        chat_id: int,
        text: str,
        corr_id: str,
    ) -> int:
        """FS4 B9 cross-process bridge: agent → bot response envelope.

        Bot process polls ``claim_next_event`` filtered by
        ``event_type='human_response'`` and routes to the matching pending
        future by ``corr_id``.
        """
        return await self.enqueue_event(
            session_id,
            "human_response",
            {"chat_id": chat_id, "corr_id": corr_id, "text": text},
        )

    async def claim_next_event_of_type(
        self,
        session_id: int,
        event_type: str,
    ) -> dict[str, Any] | None:
        """Atomically claim the oldest unconsumed event of a specific type.

        FS4 B9: bot's response-polling loop needs to dequeue only
        ``human_response`` rows without touching unrelated event_queue traffic.
        Same atomic UPDATE-RETURNING contract as ``claim_next_event``.
        """
        async with connect(self.db_path) as conn:
            await conn.execute("BEGIN IMMEDIATE")
            try:
                cur = await conn.execute(
                    """
                    UPDATE event_queue
                       SET consumed_at = ?
                     WHERE id = (
                         SELECT id FROM event_queue
                          WHERE session_id = ?
                            AND event_type = ?
                            AND consumed_at IS NULL
                          ORDER BY id ASC
                          LIMIT 1
                       )
                       AND consumed_at IS NULL
                    RETURNING id, event_type, payload_json, emitted_at
                    """,
                    (_utc_now(), session_id, event_type),
                )
                row = await cur.fetchone()
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise
            if row is None:
                return None
            return {
                "id": row["id"],
                "event_type": row["event_type"],
                "payload": json.loads(row["payload_json"]),
                "emitted_at": row["emitted_at"],
            }

    async def claim_next_event(self, session_id: int) -> dict[str, Any] | None:
        """Atomically claim the oldest unconsumed event, return its dict.

        Race-free across concurrent callers — uses `UPDATE … WHERE id = (SELECT MIN(id)
        … AND consumed_at IS NULL) AND consumed_at IS NULL RETURNING …` so the
        WHERE clause re-checks the predicate at update time (SQLite 3.35+).
        The whole statement is one atomic write; two concurrent callers cannot
        both claim the same row.
        """
        async with connect(self.db_path) as conn:
            await conn.execute("BEGIN IMMEDIATE")
            try:
                cur = await conn.execute(
                    """
                    UPDATE event_queue
                       SET consumed_at = ?
                     WHERE id = (
                         SELECT id FROM event_queue
                          WHERE session_id = ? AND consumed_at IS NULL
                          ORDER BY id ASC
                          LIMIT 1
                       )
                       AND consumed_at IS NULL
                    RETURNING id, event_type, payload_json, emitted_at
                    """,
                    (_utc_now(), session_id),
                )
                row = await cur.fetchone()
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise
            if row is None:
                return None
            return {
                "id": row["id"],
                "event_type": row["event_type"],
                "payload": json.loads(row["payload_json"]),
                "emitted_at": row["emitted_at"],
            }
