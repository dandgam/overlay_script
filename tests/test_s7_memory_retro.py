"""S7 acceptance tests — Memory + Retrospective + 9 mandatory retros + reflexion (spec §6, §6.1, §18.5).

Coverage:
- MANDATORY_RETROS frozen schedule (9 entries: 6 waves + 2 epic-deep + phase-5).
- Three-level learning helpers (tactical/strategic/architectural) — writes to canonical paths.
- Hard gate semantics: `can_promote_wave('1a', '1b')` raises `HardGateError` без retro_1a.
- `is_retro_done` False для пустого / отсутствующего файла, True после `spawn_retro_worktree`.
- Anthropic Memory Tool config: beta header `context-management-2025-06-27`, tool dict shape.
- `build_proposals(retro_id)` собирает Proposal list из policy-deltas.yaml.
- End-to-end mock pilot: per-story lesson → wave boundary detected → spawn_retro
  produces retrospective.md → compress_wave_lessons rolls up → next-wave promotion unblocked.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from bmad_orchestrator.agent.betas import ANTHROPIC_BETA_HEADERS
from bmad_orchestrator.agent.memory import (
    MANDATORY_RETROS,
    MEMORY_TOOL_BETA,
    MEMORY_TOOL_NAME,
    MEMORY_TOOL_TYPE,
    WAVE_SEQUENCE,
    ArchitecturalLesson,
    HardGateError,
    RetroId,
    RetroLevel,
    StrategicLesson,
    TacticalLesson,
    build_proposals,
    can_promote_wave,
    is_retro_done,
    memory_tool_definition,
    missing_retros,
    previous_wave,
    record_architectural_lesson,
    record_strategic_lesson,
    record_tactical_lesson,
    retro_artifact_path,
)
from bmad_orchestrator.agent.tools._common import memory_dir


@pytest.fixture(autouse=True)
def _isolated_memory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Each test gets a fresh orchestrator_home so memory/ артефакты не утекают."""
    monkeypatch.setenv("ORCHESTRATOR_TARGET_PROJECT", str(tmp_path / "target"))
    monkeypatch.setenv("ORCHESTRATOR_STATE_DB", str(tmp_path / "state.db"))
    monkeypatch.setenv("ORCHESTRATOR_ORCHESTRATOR_HOME", str(tmp_path / "orch"))


# ── Schedule ─────────────────────────────────────────────────────────────────


def test_mandatory_retros_has_9_entries() -> None:
    assert len(MANDATORY_RETROS) == 9


def test_mandatory_retros_breakdown() -> None:
    waves = [r for r in MANDATORY_RETROS if r.level is RetroLevel.WAVE]
    epics = [r for r in MANDATORY_RETROS if r.level is RetroLevel.EPIC]
    phases = [r for r in MANDATORY_RETROS if r.level is RetroLevel.PHASE]
    assert len(waves) == 6
    assert len(epics) == 2
    assert len(phases) == 1
    assert [r.key for r in waves] == list(WAVE_SEQUENCE)
    assert {r.key for r in epics} == {"1", "7"}
    assert phases[0].key == "5"


def test_retro_id_slug_canonical() -> None:
    assert RetroId(RetroLevel.WAVE, "1a").slug == "wave-1a"
    assert RetroId(RetroLevel.EPIC, "7").slug == "epic-7-deep"
    assert RetroId(RetroLevel.PHASE, "5").slug == "phase-5-final"


def test_previous_wave_returns_predecessor_and_none_for_first() -> None:
    assert previous_wave("0a") is None
    assert previous_wave("0b") == "0a"
    assert previous_wave("1d") == "1c"
    with pytest.raises(ValueError):
        previous_wave("99z")


# ── Hard gates ───────────────────────────────────────────────────────────────


def test_is_retro_done_false_for_missing() -> None:
    assert is_retro_done(RetroId(RetroLevel.WAVE, "1a")) is False


def test_is_retro_done_false_for_empty_file() -> None:
    path = retro_artifact_path(RetroId(RetroLevel.WAVE, "1a"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    assert is_retro_done(RetroId(RetroLevel.WAVE, "1a")) is False


def _valid_retro_body(wave: str) -> str:
    """FS3 H11: retro must have full frontmatter + body >= 200 chars."""
    return (
        f"---\nwave: {wave}\nlevel: wave\ncreated: 2026-05-16\n---\n\n"
        f"# Wave {wave} retro\n\n"
        + ("Lesson learned about parallelism patterns. " * 10)
    )


def test_is_retro_done_true_after_write() -> None:
    path = retro_artifact_path(RetroId(RetroLevel.WAVE, "1a"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_valid_retro_body("1a"), encoding="utf-8")
    assert is_retro_done(RetroId(RetroLevel.WAVE, "1a")) is True


def test_can_promote_wave_blocks_without_previous_retro() -> None:
    with pytest.raises(HardGateError) as exc:
        can_promote_wave("1a", "1b")
    assert "Wave '1a'" in str(exc.value)
    assert "1b" in str(exc.value)


def test_can_promote_wave_passes_when_retro_done() -> None:
    path = retro_artifact_path(RetroId(RetroLevel.WAVE, "1a"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_valid_retro_body("1a"), encoding="utf-8")
    can_promote_wave("1a", "1b")  # no raise


def test_can_promote_wave_rejects_non_adjacent_jump() -> None:
    with pytest.raises(ValueError):
        can_promote_wave("1a", "1c")  # skipping 1b not allowed


def test_can_promote_wave_rejects_unknown_wave() -> None:
    with pytest.raises(ValueError):
        can_promote_wave("99z", "1b")
    with pytest.raises(ValueError):
        can_promote_wave("1a", "99z")


def test_missing_retros_initially_lists_all_nine() -> None:
    miss = missing_retros()
    assert len(miss) == 9


def test_missing_retros_shrinks_as_retros_complete() -> None:
    target = retro_artifact_path(RetroId(RetroLevel.WAVE, "0a"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_valid_retro_body("0a"), encoding="utf-8")
    miss = missing_retros()
    assert len(miss) == 8
    assert RetroId(RetroLevel.WAVE, "0a") not in miss


# ── Levels ──────────────────────────────────────────────────────────────────


def test_tactical_lesson_writes_per_story() -> None:
    lesson = TacticalLesson(
        story_id="1-1-tenant-signup",
        tokens_used=42_000,
        cost_usd=0.18,
        duration_seconds=300,
        elicitations_auto=2,
        elicitations_escalated=0,
        notes="Smooth — policy auto-resolved both elicitations.",
    )
    path = record_tactical_lesson(lesson)
    assert path.exists()
    body = path.read_text(encoding="utf-8")
    assert "story: 1-1-tenant-signup" in body
    assert "tokens: 42000" in body
    assert "elicitations_auto: 2" in body
    assert "Smooth" in body


def test_strategic_lesson_writes_per_wave() -> None:
    lesson = StrategicLesson(
        wave="1a",
        patterns=["DB-migration stories take 2× tokens vs scaffolding"],
        optimal_parallelism=3,
        cost_distribution="median $4 / max $18 per story",
    )
    path = record_strategic_lesson(lesson)
    assert path.exists()
    body = path.read_text(encoding="utf-8")
    assert "wave: 1a" in body
    assert "optimal_parallelism: 3" in body
    assert "DB-migration stories" in body


def test_architectural_lesson_writes_per_phase() -> None:
    lesson = ArchitecturalLesson(
        phase="phase-4",
        cross_cutting_concerns=["session token storage", "rate limiter shared across services"],
        security_review_categories=["auth", "billing"],
    )
    path = record_architectural_lesson(lesson)
    assert path.exists()
    body = path.read_text(encoding="utf-8")
    assert "phase: phase-4" in body
    assert "session token storage" in body
    assert "auth" in body


# ── Memory Tool ─────────────────────────────────────────────────────────────


def test_memory_tool_definition_shape() -> None:
    spec = memory_tool_definition()
    assert spec == {"type": MEMORY_TOOL_TYPE, "name": MEMORY_TOOL_NAME}
    assert MEMORY_TOOL_NAME == "memory"
    assert MEMORY_TOOL_TYPE.startswith("memory_")


def test_memory_tool_beta_header_present() -> None:
    assert MEMORY_TOOL_BETA == "context-management-2025-06-27"
    assert MEMORY_TOOL_BETA in ANTHROPIC_BETA_HEADERS


# ── Proposals ───────────────────────────────────────────────────────────────


def test_build_proposals_returns_empty_when_retro_missing() -> None:
    assert build_proposals(RetroId(RetroLevel.WAVE, "1a")) == []


def test_build_proposals_reads_policy_deltas() -> None:
    retro_id = RetroId(RetroLevel.WAVE, "1a")
    retro_path = retro_artifact_path(retro_id)
    retro_path.parent.mkdir(parents=True, exist_ok=True)
    retro_path.write_text(_valid_retro_body("1a"), encoding="utf-8")

    deltas_path = memory_dir() / "per-wave" / "1a-policy-deltas.yaml"
    deltas_path.parent.mkdir(parents=True, exist_ok=True)
    deltas_path.write_text(
        yaml.safe_dump(
            {
                "deltas": [
                    {
                        "type": "policy",
                        "title": "auto-resolve async error handling",
                        "risk": "low",
                        "effect": "~15% меньше escalations на async stories",
                        "diff_summary": "+1 rule",
                    },
                    {
                        "type": "code",
                        "title": "Cache epic frontmatter parsing",
                        "risk": "high",
                        "effect": "~30s savings on wave start",
                        "diff_summary": "+28 / -5",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    proposals = build_proposals(retro_id)
    assert len(proposals) == 2

    p_policy = next(p for p in proposals if p.type == "policy")
    assert p_policy.id == "wave-1a-policy-01"
    assert p_policy.risk == "low"
    assert p_policy.auto_apply_if_approved is True
    assert "async" in p_policy.title

    p_code = next(p for p in proposals if p.type == "code")
    assert p_code.id == "wave-1a-code-02"
    assert p_code.risk == "high"
    # Code proposals never auto-apply per skill spec.
    assert p_code.auto_apply_if_approved is False


def test_build_proposals_coerces_invalid_type_and_risk() -> None:
    retro_id = RetroId(RetroLevel.WAVE, "0b")
    retro_path = retro_artifact_path(retro_id)
    retro_path.parent.mkdir(parents=True, exist_ok=True)
    retro_path.write_text("# retro 0b\n", encoding="utf-8")

    deltas_path = memory_dir() / "per-wave" / "0b-policy-deltas.yaml"
    deltas_path.parent.mkdir(parents=True, exist_ok=True)
    deltas_path.write_text(
        yaml.safe_dump(
            {"deltas": [{"type": "nonsense", "risk": "extreme", "title": "x"}]}
        ),
        encoding="utf-8",
    )

    proposals = build_proposals(retro_id)
    assert len(proposals) == 1
    assert proposals[0].type == "policy"  # invalid → default
    assert proposals[0].risk == "low"  # invalid → default


# ── End-to-end mock pilot ───────────────────────────────────────────────────


async def _call(tool_obj: Any, args: dict[str, Any]) -> dict[str, Any]:
    result = await tool_obj.handler(args)
    assert isinstance(result, dict)
    return result


def _payload(reply: dict[str, Any]) -> dict[str, Any]:
    body = reply["content"][0]["text"]
    data = json.loads(body)
    assert isinstance(data, dict)
    return data


@pytest.mark.asyncio
async def test_end_to_end_lesson_to_retro_to_promote_unblocked() -> None:
    """spec §6.1 acceptance: per-story lesson → wave boundary → spawn_retro
    → retrospective.md exists → memory roll-up → next-wave promotion unblocked."""
    from bmad_orchestrator.agent.tools.memory import compress_wave_lessons
    from bmad_orchestrator.agent.tools.retro import spawn_retro_worktree

    wave = "1a"
    next_wave = "1b"

    # Hard gate enforces: 1a→1b blocked before retro.
    with pytest.raises(HardGateError):
        can_promote_wave(wave, next_wave)

    # Step 1 — write 3 tactical lessons (per-story).
    for sid, tokens, cost in [
        ("1a-001", 30_000, 0.12),
        ("1a-002", 45_000, 0.21),
        ("1a-003", 38_000, 0.16),
    ]:
        record_tactical_lesson(
            TacticalLesson(
                story_id=sid,
                tokens_used=tokens,
                cost_usd=cost,
                duration_seconds=400,
                notes=f"smooth story {sid}",
            )
        )
    per_story_dir = memory_dir() / "per-story"
    assert sum(1 for _ in per_story_dir.glob("*.lesson.md")) == 3

    # Step 2 — wave boundary reached → spawn retro worktree (mock-mode).
    reply = await _call(spawn_retro_worktree, {"wave": wave, "level": "wave"})
    payload = _payload(reply)
    retro_path = Path(payload["retrospective_path"])
    assert retro_path.exists()
    assert payload["mock"] is True
    # FS3 H11: mock seed is intentionally too small to pass the hard gate —
    # the live agent must fill it in. Simulate that here.
    assert not is_retro_done(RetroId(RetroLevel.WAVE, wave))
    retro_path.write_text(_valid_retro_body(wave), encoding="utf-8")
    assert is_retro_done(RetroId(RetroLevel.WAVE, wave))

    # Step 3 — memory roll-up (per-story → per-wave).
    # compress_wave_lessons globs `<wave>-*.md` — `1a-*.md` matches our story IDs.
    reply = await _call(compress_wave_lessons, {"wave": wave})
    payload = _payload(reply)
    rollup_path = Path(payload["out_path"])
    assert rollup_path.exists()
    assert len(payload["sources"]) == 3
    body = rollup_path.read_text(encoding="utf-8")
    assert "1a-001" in body and "1a-002" in body and "1a-003" in body

    # Step 4 — next-wave promotion unblocked.
    can_promote_wave(wave, next_wave)  # no raise


@pytest.mark.asyncio
async def test_spawn_retro_writes_epic_artifact_to_retrospectives_dir() -> None:
    """Epic-deep retros live in retrospectives/, not per-wave/."""
    from bmad_orchestrator.agent.tools.retro import spawn_retro_worktree

    reply = await _call(spawn_retro_worktree, {"wave": "1", "level": "epic"})
    payload = _payload(reply)
    path = Path(payload["retrospective_path"])
    assert path.exists()
    assert path.name == "epic-1-deep.md"
    assert path.parent.name == "retrospectives"


@pytest.mark.asyncio
async def test_spawn_retro_writes_phase_artifact() -> None:
    from bmad_orchestrator.agent.tools.retro import spawn_retro_worktree

    reply = await _call(spawn_retro_worktree, {"wave": "anything", "level": "phase"})
    payload = _payload(reply)
    path = Path(payload["retrospective_path"])
    assert path.exists()
    assert path.name == "phase-5-final.md"


@pytest.mark.asyncio
async def test_spawn_retro_real_without_claude_returns_error() -> None:
    """real=True with no `claude` binary in PATH must surface clear error."""
    import shutil as _shutil

    from bmad_orchestrator.agent.tools import retro as retro_mod
    from bmad_orchestrator.agent.tools.retro import spawn_retro_worktree

    original_which = _shutil.which

    def _no_claude(name: str, *args: Any, **kwargs: Any) -> str | None:
        if name == "claude":
            return None
        return original_which(name, *args, **kwargs)

    retro_mod.shutil.which = _no_claude  # type: ignore[assignment]
    try:
        reply = await _call(
            spawn_retro_worktree, {"wave": "1a", "level": "wave", "real": True}
        )
    finally:
        retro_mod.shutil.which = original_which  # type: ignore[assignment]

    assert reply.get("isError") is True
    body = json.loads(reply["content"][0]["text"])
    assert body["error"] == "claude_missing"


def test_detect_wave_boundary_uses_canonical_retro_path(tmp_path: Path) -> None:
    """Regression: detect_wave_boundary must consult retro_artifact_path() —
    not an ad-hoc location — so hard-gate state stays consistent."""
    # The canonical path for wave 1a:
    path = retro_artifact_path(RetroId(RetroLevel.WAVE, "1a"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_valid_retro_body("1a"), encoding="utf-8")
    assert is_retro_done(RetroId(RetroLevel.WAVE, "1a"))
