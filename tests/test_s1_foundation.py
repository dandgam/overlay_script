"""S1 acceptance tests — Foundation & SDK scaffold.

Покрывает acceptance criteria S1 (см. spec §22):
- ANTHROPIC_BETA_HEADERS константа с 4 флагами
- system_prompt.py cache_control ttl="1h" explicit
- state.db aiosqlite schema (agent_session, budget_tracker, event_queue)
- tests/fixtures/ загружаются
"""

from __future__ import annotations

from pathlib import Path

import pytest

# ── Beta headers (§11.1) ─────────────────────────────────────────────────────

def test_anthropic_beta_headers_has_all_four_flags() -> None:
    from bmad_orchestrator.agent.betas import ANTHROPIC_BETA_HEADERS

    assert "tool-search-tool-2025-10-19" in ANTHROPIC_BETA_HEADERS
    assert "advanced-tool-use-2025-11-20" in ANTHROPIC_BETA_HEADERS
    assert "context-management-2025-06-27" in ANTHROPIC_BETA_HEADERS
    assert "interleaved-thinking-2025-05-14" in ANTHROPIC_BETA_HEADERS
    assert len(ANTHROPIC_BETA_HEADERS) == 4


def test_settings_default_beta_headers_equal_canonical() -> None:
    from bmad_orchestrator.agent.betas import ANTHROPIC_BETA_HEADERS
    from bmad_orchestrator.config import Settings

    s = Settings()
    assert s.beta_headers == list(ANTHROPIC_BETA_HEADERS)


# ── System prompt (§11.2) ────────────────────────────────────────────────────

def test_system_prompt_blocks_have_explicit_ttl_1h() -> None:
    """С 2026-03-06 default TTL = 5min. Спека требует explicit ttl='1h'."""
    from bmad_orchestrator.agent.system_prompt import build_system_prompt

    blocks = build_system_prompt(Path("/tmp/mock-project"), wave="1a", locale="ru")

    cached_blocks = [b for b in blocks if "cache_control" in b]
    assert len(cached_blocks) >= 3, "should cache project context + ops rules + tool catalog"

    for blk in cached_blocks:
        cc = blk["cache_control"]
        assert cc["type"] == "ephemeral"
        assert cc["ttl"] == "1h", f"cache_control missing explicit ttl='1h': {cc!r}"


# ── state.db schema (§6, §11) ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_state_db_init_creates_three_tables(tmp_path: Path) -> None:
    import aiosqlite

    from bmad_orchestrator.state import init_db

    db_path = tmp_path / "state.db"
    await init_db(db_path)

    async with aiosqlite.connect(str(db_path)) as conn:
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row[0] async for row in cur}

    for required in ("agent_session", "budget_tracker", "event_queue"):
        assert required in tables, f"table {required} missing from state.db"


@pytest.mark.asyncio
async def test_state_db_session_lifecycle(tmp_path: Path) -> None:
    from bmad_orchestrator.state import StateDB

    db = StateDB(tmp_path / "state.db")
    await db.init()

    session_id = await db.create_session("mock-odyssey", "1a", max_parallel=2)
    assert session_id > 0

    await db.upsert_budget(
        session_id=session_id,
        scope="story",
        scope_target_id="1-1-tenant-signup",
        spent_usd=12.5,
        spent_tokens=40_000,
        alarm_threshold=30.0,
        halt_threshold=50.0,
    )

    eid = await db.enqueue_event(session_id, "worker_completed", {"story": "1-1"})
    assert eid > 0

    claimed = await db.claim_next_event(session_id)
    assert claimed is not None
    assert claimed["event_type"] == "worker_completed"
    assert claimed["payload"]["story"] == "1-1"

    # повторный claim должен вернуть None (event consumed)
    assert await db.claim_next_event(session_id) is None

    await db.end_session(session_id, status="stopped")


@pytest.mark.asyncio
async def test_state_db_budget_upsert_breached_flags(tmp_path: Path) -> None:
    from bmad_orchestrator.state import StateDB, connect

    db = StateDB(tmp_path / "state.db")
    await db.init()
    sid = await db.create_session("mock", "1a")

    # alarm-only
    await db.upsert_budget(sid, "story", "x", 35.0, 0, 30.0, 50.0)
    # halt
    await db.upsert_budget(sid, "story", "x", 60.0, 0, 30.0, 50.0)

    async with connect(tmp_path / "state.db") as conn:
        cur = await conn.execute(
            "SELECT breached_alarm, breached_halt FROM budget_tracker WHERE scope_target_id='x'"
        )
        row = await cur.fetchone()
        assert row is not None
        assert row["breached_alarm"] == 1
        assert row["breached_halt"] == 1


# ── fixtures load ────────────────────────────────────────────────────────────

def test_mock_odyssey_fixtures_exist() -> None:
    from tests.fixtures import MOCK_ARTIFACTS, MOCK_STORIES

    assert (MOCK_ARTIFACTS / "epics.md").exists()
    assert (MOCK_ARTIFACTS / "sprint-status.yaml").exists()

    stories = sorted(MOCK_STORIES.glob("*.md"))
    ids = [s.stem for s in stories]
    assert "1-1-tenant-signup" in ids
    assert "1-2-tenant-activate" in ids
    assert "1-3-tenant-disable" in ids
    assert "2-1-stripe-webhook" in ids
