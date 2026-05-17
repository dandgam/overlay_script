"""P1 regression tests — canonical_patches_port (Patch H + Patch C).

Spec: spec/spec_canonical_patches_port.md §P1.
Tracker: .claude/initiative-tracker-canonical_patches_port.md.

Coverage (20 tests):

* **Patch H** (5) — ``_worker_timeout_sec()`` default changed to 1800 (was
  86400); ``BMAD_WORKER_TIMEOUT_SEC`` env override semantics preserved across
  the value range (positive int / zero / negative / non-digit / unset).
* **Patch C policy** (5) — ``load_deletion_safety_policy`` happy path, missing
  file, malformed YAML, non-mapping top-level, schema violation.
* **Patch C matcher** (5) — ``match_deletions`` matches by basename, by full
  path, by glob; empty inputs; non-pattern paths ignored.
* **Patch C subscriber** (5) — wired before code_review in ``_run_real_pilot``
  (order asserted); halts WORKER_COMPLETED on unsafe deletion (status mutated
  to ``halted_unsafe_deletion``, HUMAN_QUERY emitted); no-op on non-success
  status; no-op on success without unsafe paths; no-op on non-WORKER_COMPLETED
  event types.
"""

from __future__ import annotations

import asyncio
import importlib
import subprocess
from pathlib import Path

import pytest

from bmad_orchestrator.agent.run import _run_real_pilot
from bmad_orchestrator.agent.safety.budget_guard import BudgetGuard
from bmad_orchestrator.config import ModelConfig, load_settings
from bmad_orchestrator.runtime import worker_spawn
from bmad_orchestrator.runtime.deletion_safety import (
    DELETION_SAFETY_POLICY_PATH_DEFAULT,
    DeletionSafetyPolicy,
    deletion_safety_subscriber,
    load_deletion_safety_policy,
    match_deletions,
)
from bmad_orchestrator.runtime.event_loop import Event, EventLoop, EventType
from bmad_orchestrator.skills_repo import PolicyInvalidError, PolicyNotFoundError

# ─────────────────────────── Patch H tests ────────────────────────────────────


def test_patch_h_default_is_1800_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default timeout is the canonical 30 min (1800 s), NOT the legacy 24 h."""
    monkeypatch.delenv("BMAD_WORKER_TIMEOUT_SEC", raising=False)
    assert worker_spawn._worker_timeout_sec() == 1800


def test_patch_h_env_override_positive_int_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A positive-int env override remains honoured (backward compat)."""
    monkeypatch.setenv("BMAD_WORKER_TIMEOUT_SEC", "7200")
    assert worker_spawn._worker_timeout_sec() == 7200


def test_patch_h_env_override_zero_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``BMAD_WORKER_TIMEOUT_SEC=0`` is invalid → falls back to default 1800."""
    monkeypatch.setenv("BMAD_WORKER_TIMEOUT_SEC", "0")
    assert worker_spawn._worker_timeout_sec() == 1800


def test_patch_h_env_override_non_digit_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-digit junk → ignored; default returned."""
    monkeypatch.setenv("BMAD_WORKER_TIMEOUT_SEC", "thirty-minutes")
    assert worker_spawn._worker_timeout_sec() == 1800


def test_patch_h_env_override_negative_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Negative values are non-digit per ``.isdigit()`` → fall back to default."""
    monkeypatch.setenv("BMAD_WORKER_TIMEOUT_SEC", "-60")
    assert worker_spawn._worker_timeout_sec() == 1800


# ────────────────────── Patch C — policy loader ──────────────────────────────


def _write_policy(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "deletion-safety.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def test_policy_load_happy_path(tmp_path: Path) -> None:
    p = _write_policy(
        tmp_path,
        "patterns:\n  - 'migrations/*.sql'\n  - '*.env'\nescalation_text: hi\n",
    )
    policy = load_deletion_safety_policy(p)
    assert policy.patterns == ["migrations/*.sql", "*.env"]
    assert policy.escalation_text == "hi"


def test_policy_load_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(PolicyNotFoundError):
        load_deletion_safety_policy(tmp_path / "nope.yaml")


def test_policy_load_malformed_yaml_raises(tmp_path: Path) -> None:
    p = _write_policy(tmp_path, "patterns: [unterminated\n")
    with pytest.raises(PolicyInvalidError):
        load_deletion_safety_policy(p)


def test_policy_load_non_mapping_raises(tmp_path: Path) -> None:
    p = _write_policy(tmp_path, "- just\n- a\n- list\n")
    with pytest.raises(PolicyInvalidError):
        load_deletion_safety_policy(p)


def test_policy_load_schema_violation_raises(tmp_path: Path) -> None:
    """``patterns`` must be a list of strings; mapping for value fails validation."""
    p = _write_policy(tmp_path, "patterns: 42\n")
    with pytest.raises(PolicyInvalidError):
        load_deletion_safety_policy(p)


# ───────────────────── Patch C — match_deletions ─────────────────────────────


@pytest.fixture
def policy() -> DeletionSafetyPolicy:
    return DeletionSafetyPolicy(
        patterns=[
            "migrations/*.sql",
            "*.env",
            "*secrets*",
            "*.pem",
        ],
        escalation_text="unsafe",
    )


def test_match_deletions_basename_match(policy: DeletionSafetyPolicy) -> None:
    hits = match_deletions(["src/config/prod.env"], policy)
    assert [h.pattern for h in hits] == ["*.env"]
    assert hits[0].path == "src/config/prod.env"


def test_match_deletions_full_path_match(policy: DeletionSafetyPolicy) -> None:
    hits = match_deletions(["migrations/2026_05_index.sql"], policy)
    assert [h.pattern for h in hits] == ["migrations/*.sql"]


def test_match_deletions_substring_match(policy: DeletionSafetyPolicy) -> None:
    hits = match_deletions(["config/app_secrets.json"], policy)
    assert [h.pattern for h in hits] == ["*secrets*"]


def test_match_deletions_no_match_returns_empty(
    policy: DeletionSafetyPolicy,
) -> None:
    assert match_deletions(["src/main.rs", "README.md"], policy) == []


def test_match_deletions_handles_empty_and_whitespace(
    policy: DeletionSafetyPolicy,
) -> None:
    assert match_deletions([], policy) == []
    assert match_deletions(["", "   "], policy) == []


# ───────────────── Patch C — subscriber wiring + behaviour ───────────────────


def _make_target_with_stories(tmp_path: Path) -> Path:
    target = tmp_path / "proj"
    target.mkdir(exist_ok=True)
    artifacts = target / "_bmad-output" / "planning-artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "sprint-status.yaml").write_text(
        "wave: w\nepics: {}\n", encoding="utf-8"
    )
    (artifacts / "stories").mkdir(exist_ok=True)
    return target


@pytest.mark.asyncio
async def test_deletion_safety_wired_before_code_review(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Patch C subscriber must precede code_review_subscriber on the bus so
    its payload mutation (status → 'halted_unsafe_deletion') runs BEFORE
    code_review_subscriber gates on status == 'success'.

    After P2 (Patch N, 2026-05-18) build_check_subscriber registers first, so
    deletion_safety is at index 1 (not 0). Both still precede code_review.
    """
    from bmad_orchestrator.runtime.build_check import build_check_subscriber

    monkeypatch.delenv("BMAD_REQUIRE_SANDBOX", raising=False)
    target = _make_target_with_stories(tmp_path)
    monkeypatch.setenv("ORCHESTRATOR_TARGET_PROJECT", str(target))

    bus = EventLoop()
    budget = BudgetGuard(load_settings().budget, event_loop=bus)
    try:
        await _run_real_pilot(
            bus, project="proj", wave="w", max_parallel=1, max_stories=1,
            max_spend_usd=10.0, budget=budget, state_db=None, session_id=None,
            models=ModelConfig(), options={},
        )
    finally:
        await bus.stop()

    # P1 wired 4 subscribers (deletion + review + merge + sweep); P2 adds
    # build_check at index 0, total 5.
    assert len(bus._subs) == 5
    funcs = [getattr(s, "func", s) for s in bus._subs]
    assert funcs[0] is build_check_subscriber, (
        f"Patch N subscriber must be wired first; got {funcs[0]!r}"
    )
    assert funcs[1] is deletion_safety_subscriber, (
        f"Patch C subscriber must be wired second; got {funcs[1]!r}"
    )


def _init_git_worktree_with_deletion(
    tmp: Path, deleted_path: str, content: str = "x\n"
) -> Path:
    """Init a git repo in ``tmp``, commit a file, then commit its deletion.
    Returns the worktree path (== ``tmp``). HEAD commit's diff has 1 deletion."""
    subprocess.run(["git", "init", "-q", str(tmp)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp), "config", "user.email", "t@t"], check=True
    )
    subprocess.run(
        ["git", "-C", str(tmp), "config", "user.name", "t"], check=True
    )
    target = tmp / deleted_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(tmp), "commit", "-q", "-m", "add"], check=True
    )
    target.unlink()
    subprocess.run(["git", "-C", str(tmp), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(tmp), "commit", "-q", "-m", "rm"], check=True
    )
    return tmp


@pytest.mark.asyncio
async def test_deletion_safety_halts_on_unsafe_deletion(tmp_path: Path) -> None:
    """Worker diff includes ``migrations/x.sql`` deletion → status mutated to
    ``halted_unsafe_deletion``, HUMAN_QUERY emitted with offending path."""
    worktree = _init_git_worktree_with_deletion(
        tmp_path / "wt", "migrations/2026_05_x.sql"
    )
    bus = EventLoop()
    ev = Event(
        type=EventType.WORKER_COMPLETED,
        payload={
            "story_id": "s1",
            "worktree": str(worktree),
            "status": "success",
        },
    )
    await deletion_safety_subscriber(ev, bus)

    assert ev.payload["status"] == "halted_unsafe_deletion"
    assert ev.payload["halt_reason"] == "deletion_safety"
    assert ev.payload["unsafe_deletions"] == ["migrations/2026_05_x.sql"]

    drained: list[Event] = []
    while True:
        nxt = await bus.next(timeout=0.01)
        if nxt is None:
            break
        drained.append(nxt)
    human_queries = [e for e in drained if e.type == EventType.HUMAN_QUERY]
    assert len(human_queries) == 1
    assert "migrations/2026_05_x.sql" in human_queries[0].payload["text"]


@pytest.mark.asyncio
async def test_deletion_safety_noop_on_safe_deletion(tmp_path: Path) -> None:
    """Worker deletes a non-protected file → no halt, no HUMAN_QUERY."""
    worktree = _init_git_worktree_with_deletion(tmp_path / "wt", "docs/old.md")
    bus = EventLoop()
    ev = Event(
        type=EventType.WORKER_COMPLETED,
        payload={
            "story_id": "s1",
            "worktree": str(worktree),
            "status": "success",
        },
    )
    await deletion_safety_subscriber(ev, bus)
    assert ev.payload["status"] == "success"
    assert "halt_reason" not in ev.payload
    assert await bus.next(timeout=0.01) is None


@pytest.mark.asyncio
async def test_deletion_safety_noop_on_non_success_status(tmp_path: Path) -> None:
    """Pre-failed worker → subscriber must not even shell out to git."""
    bus = EventLoop()
    ev = Event(
        type=EventType.WORKER_COMPLETED,
        payload={
            "story_id": "s1",
            "worktree": "/nonexistent/path",
            "status": "failed",
        },
    )
    await deletion_safety_subscriber(ev, bus)
    assert ev.payload["status"] == "failed"
    assert await bus.next(timeout=0.01) is None


@pytest.mark.asyncio
async def test_deletion_safety_noop_on_unrelated_event_type() -> None:
    """Any event other than WORKER_COMPLETED is a fast no-op (no git, no
    bus emit, no payload mutation)."""
    bus = EventLoop()
    ev = Event(
        type=EventType.WAVE_BOUNDARY_REACHED,
        payload={"completed_stories": 10},
    )
    await deletion_safety_subscriber(ev, bus)
    assert ev.payload == {"completed_stories": 10}
    assert await bus.next(timeout=0.01) is None


# ─────────────────── Bundled policy file sanity check ────────────────────────


def test_default_policy_file_loads_and_contains_baseline_patterns() -> None:
    """The on-disk skills/policy/deletion-safety.yaml must validate and carry
    at least the migrations + secrets + env baseline patterns."""
    policy = load_deletion_safety_policy(DELETION_SAFETY_POLICY_PATH_DEFAULT)
    joined = "|".join(policy.patterns)
    assert "migrations" in joined
    assert "*.env" in policy.patterns
    assert "*.pem" in policy.patterns
    assert policy.escalation_text.strip() != ""


# Module-import smoke: re-import deletion_safety to assert no top-level side
# effects (the subscriber must be safe to register without an event bus).
def test_deletion_safety_module_reimport_clean() -> None:
    importlib.reload(
        importlib.import_module("bmad_orchestrator.runtime.deletion_safety")
    )


# Asyncio-loop reach: subscriber may be awaited from a non-running asyncio
# context inside a test (uses asyncio.create_subprocess_exec). This guards the
# happy-path coroutine signature against accidental sync conversion.
def test_subscriber_is_async_coroutine_function() -> None:
    assert asyncio.iscoroutinefunction(deletion_safety_subscriber)
