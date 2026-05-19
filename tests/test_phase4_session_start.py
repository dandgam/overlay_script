"""Phase 4 hardening #1 — SessionStart hook: force-load worker policy.

Spec: spec_phase4_hardening §1.1.

Coverage (10 tests):

Unit — build_session_start_block for 5 canonical skills:
  * bmad-dev-story
  * bmad-code-review
  * bmad-investigate
  * bmad-correct-course
  * bmad-sprint-planning
  Each: block contains skill_slug, story_id, policy excerpt keywords.

Unit — inject_into_worker_env (3 tests):
  * Env merge: bootstrap key present in result.
  * Env preserved untouched: input env not mutated, other keys preserved.
  * Idempotent: repeated injection replaces (not appends) the key.

Integration — worker_spawn env contains ORCHESTRATOR_SESSION_BOOTSTRAP (2 tests):
  * Mock spawn sets key in merged_env via _build_worker_env chain.
  * Non-empty payload in the env-var.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bmad_orchestrator.agent.safety.session_start import (
    SESSION_BOOTSTRAP_ENV_KEY,
    build_session_start_block,
    inject_into_worker_env,
)

# ────────────────────────────────────────────────────────────────────────────
# A. Unit — build_session_start_block for 5 canonical skills (5 tests)
# ────────────────────────────────────────────────────────────────────────────


def _assert_block_contains_basics(block: str, skill_slug: str, story_id: str) -> None:
    """Helper: assert all required fields are present in the block."""
    assert skill_slug in block, f"Expected skill_slug {skill_slug!r} in block"
    assert story_id in block, f"Expected story_id {story_id!r} in block"
    # Policy excerpt keywords: each section title must appear
    assert "NO MERGE WITHOUT GATE PASS" in block
    assert "3-ATTEMPT CAP" in block
    assert "EVIDENCE-BASED COMPLETION CLAIMS" in block
    assert "SANDBOX BOUNDARIES" in block
    # Phase marker
    assert "BMad Phase 4" in block
    # Header
    assert "Worker session bootstrap" in block


def test_session_start_block_bmad_dev_story() -> None:
    """build_session_start_block for bmad-dev-story contains skill_slug + story_id + policy."""
    block = build_session_start_block("bmad-dev-story", "story-1.1")
    _assert_block_contains_basics(block, "bmad-dev-story", "story-1.1")


def test_session_start_block_bmad_code_review() -> None:
    """build_session_start_block for bmad-code-review contains skill_slug + story_id + policy."""
    block = build_session_start_block("bmad-code-review", "story-2.3")
    _assert_block_contains_basics(block, "bmad-code-review", "story-2.3")


def test_session_start_block_bmad_investigate() -> None:
    """build_session_start_block for bmad-investigate contains skill_slug + story_id + policy."""
    block = build_session_start_block("bmad-investigate", "story-3.0")
    _assert_block_contains_basics(block, "bmad-investigate", "story-3.0")


def test_session_start_block_bmad_correct_course() -> None:
    """build_session_start_block for bmad-correct-course contains skill_slug + story_id + policy."""
    block = build_session_start_block("bmad-correct-course", "story-4.2")
    _assert_block_contains_basics(block, "bmad-correct-course", "story-4.2")


def test_session_start_block_bmad_sprint_planning() -> None:
    """build_session_start_block for bmad-sprint-planning contains skill_slug + story_id + policy."""
    block = build_session_start_block("bmad-sprint-planning", "story-sprint-01")
    _assert_block_contains_basics(block, "bmad-sprint-planning", "story-sprint-01")


# ────────────────────────────────────────────────────────────────────────────
# B. Unit — inject_into_worker_env (3 tests)
# ────────────────────────────────────────────────────────────────────────────


def test_inject_bootstrap_key_present() -> None:
    """inject_into_worker_env places SESSION_BOOTSTRAP_ENV_KEY in result."""
    env = {"PATH": "/usr/bin"}
    block = "test bootstrap content"
    result = inject_into_worker_env(env, block)
    assert SESSION_BOOTSTRAP_ENV_KEY in result
    assert result[SESSION_BOOTSTRAP_ENV_KEY] == block


def test_inject_env_preserved_untouched() -> None:
    """inject_into_worker_env does not mutate the input dict; other keys preserved."""
    original_env = {"PATH": "/usr/bin", "HOME": "/home/user", "LANG": "en_US.UTF-8"}
    block = "bootstrap block"
    result = inject_into_worker_env(original_env, block)

    # Original dict not mutated.
    assert SESSION_BOOTSTRAP_ENV_KEY not in original_env
    # All original keys preserved in result.
    for k, v in original_env.items():
        assert result[k] == v


def test_inject_idempotent_replace() -> None:
    """inject_into_worker_env replaces existing key rather than appending."""
    env = {SESSION_BOOTSTRAP_ENV_KEY: "old bootstrap", "PATH": "/usr/bin"}
    block_v2 = "new bootstrap content v2"
    result = inject_into_worker_env(env, block_v2)

    assert result[SESSION_BOOTSTRAP_ENV_KEY] == block_v2
    assert "old bootstrap" not in result[SESSION_BOOTSTRAP_ENV_KEY]


# ────────────────────────────────────────────────────────────────────────────
# C. Integration — worker_spawn env contains ORCHESTRATOR_SESSION_BOOTSTRAP (2 tests)
# ────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_spawn_worker_real_mode_env_has_bootstrap(tmp_path: Path) -> None:
    """Real-mode spawn_worker produces env with ORCHESTRATOR_SESSION_BOOTSTRAP set."""
    from bmad_orchestrator.runtime.worker_spawn import spawn_worker

    # We need a real worktree path and a real-looking bin path.
    # Patch the claude binary detection to return a fake path, but patch
    # asyncio.create_subprocess_exec to avoid actually launching a process.
    fake_proc = MagicMock()
    fake_proc.pid = 42
    fake_proc.stdout = None
    fake_proc.wait = AsyncMock(return_value=0)
    # NEW-5 dirty-worktree gate runs ``git status --porcelain`` via
    # create_subprocess_exec before the real spawn — tmp_path is not a git
    # repo, so emulate git exiting non-zero (gate treats it as not-applicable).
    fake_proc.returncode = 1
    fake_proc.communicate = AsyncMock(return_value=(b"", b""))

    captured_env: dict[str, str] = {}

    async def fake_exec(*args: object, **kwargs: object) -> MagicMock:
        env_arg = kwargs.get("env")
        if isinstance(env_arg, dict):
            captured_env.update(env_arg)
        return fake_proc

    with (
        patch("bmad_orchestrator.runtime.worker_spawn._resolve_claude_bin", return_value="/usr/bin/claude"),
        patch("asyncio.create_subprocess_exec", side_effect=fake_exec),
    ):
        await spawn_worker(
            worktree=str(tmp_path),
            story_id="s-integration-1",
            branch="feature/s-integration-1",
            mock=False,
        )

    assert SESSION_BOOTSTRAP_ENV_KEY in captured_env, (
        f"Expected {SESSION_BOOTSTRAP_ENV_KEY} in env; got keys: {list(captured_env)}"
    )
    assert captured_env[SESSION_BOOTSTRAP_ENV_KEY], "Bootstrap env-var must be non-empty"


@pytest.mark.asyncio
async def test_spawn_worker_bootstrap_contains_story_id(tmp_path: Path) -> None:
    """Bootstrap block in env-var contains the story_id from the spawn call."""
    from bmad_orchestrator.runtime.worker_spawn import spawn_worker

    fake_proc = MagicMock()
    fake_proc.pid = 99
    fake_proc.stdout = None
    fake_proc.wait = AsyncMock(return_value=0)
    # NEW-5 dirty-worktree gate runs ``git status --porcelain`` via
    # create_subprocess_exec before the real spawn — tmp_path is not a git
    # repo, so emulate git exiting non-zero (gate treats it as not-applicable).
    fake_proc.returncode = 1
    fake_proc.communicate = AsyncMock(return_value=(b"", b""))

    captured_env: dict[str, str] = {}

    async def fake_exec(*args: object, **kwargs: object) -> MagicMock:
        env_arg = kwargs.get("env")
        if isinstance(env_arg, dict):
            captured_env.update(env_arg)
        return fake_proc

    target_story = "epic-2-story-7"

    with (
        patch("bmad_orchestrator.runtime.worker_spawn._resolve_claude_bin", return_value="/usr/bin/claude"),
        patch("asyncio.create_subprocess_exec", side_effect=fake_exec),
    ):
        await spawn_worker(
            worktree=str(tmp_path),
            story_id=target_story,
            branch=f"feature/{target_story}",
            mock=False,
        )

    bootstrap = captured_env.get(SESSION_BOOTSTRAP_ENV_KEY, "")
    assert target_story in bootstrap, (
        f"Expected story_id {target_story!r} in bootstrap block; got: {bootstrap[:200]}"
    )
