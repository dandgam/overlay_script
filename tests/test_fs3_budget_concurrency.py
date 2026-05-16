"""FS3 acceptance tests — budget aggregator + atomic concurrency + retro
overwrite protection + subprocess timeouts (spec §B5, §H11, §H15, §M5).

Coverage:
- B5  TokenUsage + MODEL_PRICING_USD_PER_MTOK + usd_cost(Decimal) precision.
- B5  upsert_budget additive (concurrent gather → sum, not last-write-wins).
- B5  BudgetGuard.enforce_day daily limit.
- B5  NaN / inf / negative spent_usd → halt + budget_corruption audit.
- H11 record_*_lesson exclusive create (refuse overwrite).
- H11 append_retro_artifact writes timestamped sibling.
- H11 can_promote_wave content schema check (frontmatter + min body).
- H15 git_merge subprocess timeout fires SUBPROCESS_TIMEOUT_SEC.
- M5  claim_next_event atomic — 10 concurrent claims yield ≤ row count.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from bmad_orchestrator.agent.memory.gates import (
    HardGateError,
    can_promote_wave,
    is_retro_done,
    retro_artifact_path,
)
from bmad_orchestrator.agent.memory.levels import (
    RETRO_MIN_BODY_CHARS,
    StrategicLesson,
    TacticalLesson,
    append_retro_artifact,
    has_valid_retro_schema,
    record_strategic_lesson,
    record_tactical_lesson,
)
from bmad_orchestrator.agent.memory.schedule import RetroId, RetroLevel
from bmad_orchestrator.agent.safety import audit_log_path
from bmad_orchestrator.agent.safety.budget_guard import BudgetGuard
from bmad_orchestrator.agent.tools.merge import (
    SUBPROCESS_TIMEOUT_SEC,
)
from bmad_orchestrator.agent.tools.merge import (
    git_merge as _git_merge_tool,
)
from bmad_orchestrator.config import BudgetConfig
from bmad_orchestrator.runtime.budget import (
    MODEL_PRICING_USD_PER_MTOK,
    TokenUsage,
    is_finite_spend,
    usd_cost,
)
from bmad_orchestrator.state import StateDB

git_merge = _git_merge_tool.handler


# ── fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = Path(__file__).parent / "fixtures" / "mock-odyssey"
    dst = tmp_path / "mock-odyssey"
    shutil.copytree(src, dst)
    monkeypatch.setenv("ORCHESTRATOR_TARGET_PROJECT", str(dst))
    monkeypatch.setenv("ORCHESTRATOR_ORCHESTRATOR_HOME", str(tmp_path / "orch"))
    monkeypatch.setenv("ORCHESTRATOR_STATE_DB", str(tmp_path / "state.db"))
    monkeypatch.setenv("BMAD_AUDIT_LOG", str(tmp_path / "audit.events.jsonl"))


@pytest.fixture
async def db(tmp_path: Path) -> StateDB:
    db = StateDB(tmp_path / "state.db")
    await db.init()
    return db


def _read_audit() -> list[dict[str, Any]]:
    path = audit_log_path()
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ── B5 — TokenUsage + MODEL_PRICING + usd_cost ────────────────────────────


def test_token_usage_rejects_negative() -> None:
    with pytest.raises(ValueError):
        TokenUsage(input_tokens=-1)
    with pytest.raises(ValueError):
        TokenUsage(output_tokens=-100)


def test_token_usage_rejects_non_int() -> None:
    with pytest.raises(TypeError):
        TokenUsage(input_tokens=1.5)  # type: ignore[arg-type]


def test_token_usage_total() -> None:
    u = TokenUsage(
        input_tokens=10, cache_creation_input_tokens=20,
        cache_read_input_tokens=30, output_tokens=40,
    )
    assert u.total_tokens == 100


def test_usd_cost_sonnet_input_million() -> None:
    # 1M input tokens on sonnet 4.6 = $3.00 exact (Decimal).
    cost = usd_cost("claude-sonnet-4-6", TokenUsage(input_tokens=1_000_000))
    assert cost == Decimal("3")
    assert isinstance(cost, Decimal)


def test_usd_cost_opus_output_million() -> None:
    cost = usd_cost("claude-opus-4-7", TokenUsage(output_tokens=1_000_000))
    assert cost == Decimal("75")


def test_usd_cost_haiku_all_dimensions() -> None:
    # haiku 4.5: 1M in × $1 + 1M out × $5 + 1M cache_write × $1.25 + 1M cache_read × $0.10 = $7.35
    cost = usd_cost(
        "claude-haiku-4-5",
        TokenUsage(
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            cache_creation_input_tokens=1_000_000,
            cache_read_input_tokens=1_000_000,
        ),
    )
    assert cost == Decimal("7.35")


def test_usd_cost_unknown_model_raises() -> None:
    with pytest.raises(ValueError, match="unknown model"):
        usd_cost("gpt-4", TokenUsage(input_tokens=100))


def test_pricing_table_contains_all_current_models() -> None:
    for m in ("claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4-5"):
        assert m in MODEL_PRICING_USD_PER_MTOK
        pricing = MODEL_PRICING_USD_PER_MTOK[m]
        assert {"input", "output", "cache_write_1h", "cache_read"} <= pricing.keys()


def test_is_finite_spend() -> None:
    assert is_finite_spend(0.0) is True
    assert is_finite_spend(100.0) is True
    assert is_finite_spend(Decimal("5.50")) is True
    assert is_finite_spend(-0.01) is False
    assert is_finite_spend(float("inf")) is False
    assert is_finite_spend(float("-inf")) is False
    assert is_finite_spend(float("nan")) is False
    assert is_finite_spend(Decimal("Infinity")) is False
    assert is_finite_spend(Decimal("NaN")) is False


# ── B5 — upsert_budget additive ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_upsert_budget_additive_sequential(db: StateDB) -> None:
    sid = await db.create_session("proj", "1a")
    await db.upsert_budget(sid, "story", "1-1", 25.0, 1000, 30.0, 50.0)
    await db.upsert_budget(sid, "story", "1-1", 25.0, 1000, 30.0, 50.0)
    row = await db.get_budget(sid, "story", "1-1")
    assert row is not None
    assert row["spent_usd"] == pytest.approx(50.0)
    assert row["spent_tokens"] == 2000
    assert row["breached_alarm"] is True  # 50 >= 30
    assert row["breached_halt"] is True  # 50 >= 50


@pytest.mark.asyncio
async def test_upsert_budget_concurrent_gather_sums(db: StateDB) -> None:
    """10 concurrent additions of $5 each — total must be $50, not $5 (last-write-wins)."""
    sid = await db.create_session("proj", "1a")
    await asyncio.gather(*[
        db.upsert_budget(sid, "story", "1-1", 5.0, 100, 30.0, 50.0)
        for _ in range(10)
    ])
    row = await db.get_budget(sid, "story", "1-1")
    assert row is not None
    assert row["spent_usd"] == pytest.approx(50.0)
    assert row["spent_tokens"] == 1000


@pytest.mark.asyncio
async def test_upsert_budget_rejects_inf(db: StateDB) -> None:
    sid = await db.create_session("proj", "1a")
    with pytest.raises(ValueError):
        await db.upsert_budget(sid, "story", "1-1", float("inf"), 0, 30.0, 50.0)


@pytest.mark.asyncio
async def test_upsert_budget_rejects_negative_tokens(db: StateDB) -> None:
    sid = await db.create_session("proj", "1a")
    with pytest.raises(ValueError):
        await db.upsert_budget(sid, "story", "1-1", 5.0, -10, 30.0, 50.0)


# ── B5 — BudgetGuard NaN/inf/negative guards ──────────────────────────────


@pytest.mark.asyncio
async def test_budget_guard_inf_halts_and_audits() -> None:
    guard = BudgetGuard(BudgetConfig())
    result = await guard.enforce_story(float("inf"), story_id="x")
    assert result.level == "halt"
    assert result.corrupted is True
    audits = _read_audit()
    assert any(
        e["event_type"] == "budget_corruption" and e.get("scope") == "story"
        for e in audits
    )


@pytest.mark.asyncio
async def test_budget_guard_nan_halts() -> None:
    guard = BudgetGuard(BudgetConfig())
    result = await guard.enforce_batch(float("nan"), wave="1a")
    assert result.level == "halt"
    assert result.corrupted is True


@pytest.mark.asyncio
async def test_budget_guard_negative_halts() -> None:
    guard = BudgetGuard(BudgetConfig())
    result = await guard.enforce_day(-5.0, day="2026-05-16")
    assert result.level == "halt"
    assert result.corrupted is True


@pytest.mark.asyncio
async def test_budget_guard_enforce_day_within_limit() -> None:
    guard = BudgetGuard(BudgetConfig(daily_limit_usd=500.0))
    ok = await guard.enforce_day(499.99, day="2026-05-16")
    assert ok.level == "ok"
    halt = await guard.enforce_day(500.0, day="2026-05-16")
    assert halt.level == "halt"
    assert halt.breached_halt is True


# ── H11 — record_*_lesson exclusive create ────────────────────────────────


def test_record_tactical_lesson_refuses_overwrite(tmp_path: Path) -> None:
    record_tactical_lesson(TacticalLesson(
        story_id="1-1", tokens_used=100, cost_usd=1.0, duration_seconds=60,
    ))
    with pytest.raises(FileExistsError):
        record_tactical_lesson(TacticalLesson(
            story_id="1-1", tokens_used=200, cost_usd=2.0, duration_seconds=120,
        ))


def test_record_strategic_lesson_refuses_overwrite() -> None:
    record_strategic_lesson(StrategicLesson(wave="1a"))
    with pytest.raises(FileExistsError):
        record_strategic_lesson(StrategicLesson(wave="1a"))


def test_append_retro_artifact_writes_timestamped_sibling() -> None:
    base = retro_artifact_path(RetroId(RetroLevel.WAVE, "1a"))
    base.parent.mkdir(parents=True, exist_ok=True)
    base.write_text(
        "---\nwave: 1a\nlevel: wave\ncreated: 2026-05-16\n---\n\n"
        + ("# Original retro\n\n" + "x" * RETRO_MIN_BODY_CHARS),
        encoding="utf-8",
    )
    appended = append_retro_artifact(base, "appended body content")
    assert appended.exists()
    assert appended != base
    # original untouched
    assert "Original retro" in base.read_text()
    assert "appended body content" in appended.read_text()


# ── H11 — content schema check ────────────────────────────────────────────


def test_can_promote_wave_blocks_when_retro_missing() -> None:
    with pytest.raises(HardGateError):
        can_promote_wave("0a", "0b")


def test_can_promote_wave_blocks_when_retro_seed_only() -> None:
    """Seed file from spawn_retro_worktree (mock) is < 200 chars + no real body."""
    path = retro_artifact_path(RetroId(RetroLevel.WAVE, "0a"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\nwave: 0a\nlevel: wave\ncreated: 2026-05-16\n---\n\n"
        "# Retrospective seed\n\n_TODO_\n",
        encoding="utf-8",
    )
    assert is_retro_done(RetroId(RetroLevel.WAVE, "0a")) is False
    with pytest.raises(HardGateError):
        can_promote_wave("0a", "0b")


def test_can_promote_wave_succeeds_when_retro_valid() -> None:
    path = retro_artifact_path(RetroId(RetroLevel.WAVE, "0a"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\nwave: 0a\nlevel: wave\ncreated: 2026-05-16\n---\n\n"
        "# Wave 0a retrospective\n\n"
        + ("Lesson learned about parallelism. " * 20),  # > 200 chars
        encoding="utf-8",
    )
    assert is_retro_done(RetroId(RetroLevel.WAVE, "0a")) is True
    can_promote_wave("0a", "0b")  # no raise


def test_has_valid_retro_schema_requires_frontmatter() -> None:
    path = Path("/tmp/no_frontmatter_test.md")
    path.write_text("just body, no frontmatter, but plenty of chars " * 20)
    try:
        assert has_valid_retro_schema(path) is False
    finally:
        path.unlink(missing_ok=True)


def test_has_valid_retro_schema_requires_min_body() -> None:
    path = Path("/tmp/short_body_test.md")
    path.write_text(
        "---\nwave: 0a\nlevel: wave\ncreated: 2026-05-16\n---\n\nshort"
    )
    try:
        assert has_valid_retro_schema(path) is False
    finally:
        path.unlink(missing_ok=True)


def test_has_valid_retro_schema_requires_all_keys() -> None:
    path = Path("/tmp/missing_keys_test.md")
    path.write_text(
        "---\nwave: 0a\n---\n\n" + "x" * 250
    )
    try:
        assert has_valid_retro_schema(path) is False
    finally:
        path.unlink(missing_ok=True)


# ── H15 — subprocess timeout ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_git_merge_subprocess_timeout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A git merge subprocess that never completes must be killed and the
    tool must return code='subprocess_timeout' inside SUBPROCESS_TIMEOUT_SEC."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    # Patch SUBPROCESS_TIMEOUT_SEC to a tiny value so the test runs fast.
    monkeypatch.setattr(
        "bmad_orchestrator.agent.tools.merge.SUBPROCESS_TIMEOUT_SEC", 1
    )

    class _HungProc:
        pid = 12345
        returncode: int | None = None

        async def communicate(self) -> tuple[bytes, bytes]:
            await asyncio.sleep(10)
            return b"", b""

        def kill(self) -> None:
            self.returncode = -9

        async def wait(self) -> int:
            return -9

    async def _fake_subprocess_exec(*args: Any, **kwargs: Any) -> _HungProc:
        return _HungProc()

    monkeypatch.setattr(
        "bmad_orchestrator.agent.tools.merge.asyncio.create_subprocess_exec",
        _fake_subprocess_exec,
    )
    monkeypatch.setenv("BMAD_MAIN_MERGE_TOKEN_PATH", str(tmp_path / "token.json"))

    result = await git_merge({
        "worktree": str(repo),
        "target_branch": "feature/foo",  # non-main; doesn't need token
        "message": "test",
        "signed_token": "",
    })
    payload = json.loads(result["content"][0]["text"])
    assert payload.get("error") == "subprocess_timeout"
    assert "timed out" in payload["message"]


def test_merge_timeout_constant_value() -> None:
    """Guard: the constant exists and is the documented 300s default."""
    assert SUBPROCESS_TIMEOUT_SEC == 300


# ── M5 — claim_next_event atomic ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_claim_next_event_atomic_no_double_claim(db: StateDB) -> None:
    """10 concurrent claims on 3 events → at most 3 events claimed total,
    each only once, the rest must return None."""
    sid = await db.create_session("proj", "1a")
    for i in range(3):
        await db.enqueue_event(sid, "TEST", {"i": i})

    results = await asyncio.gather(*[db.claim_next_event(sid) for _ in range(10)])
    claimed = [r for r in results if r is not None]
    nones = [r for r in results if r is None]

    assert len(claimed) == 3
    assert len(nones) == 7
    claimed_ids = sorted(r["id"] for r in claimed)
    assert len(set(claimed_ids)) == 3  # no duplicates


@pytest.mark.asyncio
async def test_claim_next_event_returns_in_fifo_order(db: StateDB) -> None:
    sid = await db.create_session("proj", "1a")
    for i in range(3):
        await db.enqueue_event(sid, "TEST", {"i": i})

    first = await db.claim_next_event(sid)
    second = await db.claim_next_event(sid)
    third = await db.claim_next_event(sid)
    after = await db.claim_next_event(sid)

    assert first is not None and first["payload"]["i"] == 0
    assert second is not None and second["payload"]["i"] == 1
    assert third is not None and third["payload"]["i"] == 2
    assert after is None
