"""Patch S — stage 5 commit completeness subscriber (canonical port from runner.sh).

On ``WORKER_COMPLETED`` (status=success), inspects the worker's worktree for
uncommitted residue (Stage 5 should have committed everything; anything left
is a worker bug we recover from rather than silently lose). If `git status
--porcelain` shows changes:

  * ``git add -A`` stages everything.
  * ``git commit --signoff -m "<marker>"`` lands a recovery commit.
  * ``event.payload['stage5_recovery_commit_sha']`` records the resulting sha
    so downstream subscribers (Patch Q diff-size, code review) can attribute
    the additional churn to recovery rather than the worker's own commit.

Unlike build_check / deletion_safety this subscriber **does not halt** — it
fixes silently and proceeds. The whole point of Stage 5 completeness is to
prevent data-loss when a worker forgets to commit, not to escalate.

Designed to be wired FIRST in ``_run_real_pilot`` so the recovery happens
before build_check / deletion_safety / code_review observe the worktree.

Reference: ~/.claude/skills/bmad-auto-dev/scripts/bmad-auto-dev-runner.sh
Stage 5 commit completeness (lines ~566-580 of the original 1100 LOC bash
runner). Patch S 2026-05-16.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import structlog
import yaml
from pydantic import BaseModel, ValidationError

from bmad_orchestrator.runtime.event_loop import Event, EventLoop, EventType
from bmad_orchestrator.runtime.git_env import _git_commit_env
from bmad_orchestrator.skills_repo import PolicyInvalidError, PolicyNotFoundError

log = structlog.get_logger(__name__)

STAGE5_COMPLETENESS_POLICY_PATH_DEFAULT = (
    Path(__file__).resolve().parents[3]
    / "skills"
    / "policy"
    / "stage5-completeness.yaml"
)


class Stage5CompletenessPolicy(BaseModel):
    """Schema for ``skills/policy/stage5-completeness.yaml``."""

    enabled: bool = True
    commit_marker: str = (
        "Patch S recovery: auto-stage Stage 5 residue (worker forgot to commit)"
    )
    signoff: bool = True


@dataclass(slots=True, frozen=True)
class Stage5RecoveryResult:
    recovered: bool
    commit_sha: str = ""
    staged_paths: tuple[str, ...] = ()
    error: str = ""


def load_stage5_completeness_policy(
    path: Path | None = None,
) -> Stage5CompletenessPolicy:
    """Load + validate the stage 5 completeness policy yaml.

    Raises:
        PolicyNotFoundError — file missing.
        PolicyInvalidError  — YAML parse OR schema validation fail.
    """
    p = path or STAGE5_COMPLETENESS_POLICY_PATH_DEFAULT
    if not p.exists():
        raise PolicyNotFoundError(f"stage5-completeness policy not found: {p}")
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise PolicyInvalidError(f"YAML parse error in {p}: {e}") from e
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise PolicyInvalidError(
            f"top-level structure must be mapping in {p}, got {type(raw).__name__}"
        )
    try:
        return Stage5CompletenessPolicy.model_validate(raw)
    except ValidationError as e:
        raise PolicyInvalidError(
            f"stage5-completeness schema validation failed: {e}"
        ) from e


async def _git_status_porcelain(worktree: Path) -> list[str]:
    """Return list of changed paths from ``git status --porcelain`` in worktree.

    Empty list = clean tree. Returns [] on git failure too (caller treats
    empty as nothing-to-do; halting on a missing-git worktree would be hostile
    to mock/fixture tests).
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(worktree),
            "status",
            "--porcelain",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (OSError, FileNotFoundError) as e:
        log.warning("stage5_git_status_spawn_failed", worktree=str(worktree), error=str(e))
        return []
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        log.info(
            "stage5_git_status_nonzero",
            worktree=str(worktree),
            returncode=proc.returncode,
            stderr=stderr.decode("utf-8", errors="replace")[:200],
        )
        return []
    text = stdout.decode("utf-8", errors="replace")
    paths: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        # porcelain v1 format: 2-char status code + space + path
        # e.g. " M file.py", "?? new.py", "A  added.py"
        if len(line) > 3:
            paths.append(line[3:].strip())
    return paths


async def _git_add_all(worktree: Path) -> tuple[bool, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(worktree),
            "add",
            "-A",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except (OSError, FileNotFoundError) as e:
        return False, str(e)
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return False, stdout.decode("utf-8", errors="replace")[:200]
    return True, ""


async def _git_commit(
    worktree: Path, message: str, signoff: bool
) -> tuple[bool, str, str]:
    """Commit staged changes. Returns (ok, error, sha)."""
    args = ["git", "-C", str(worktree), "commit", "-m", message]
    if signoff:
        args.append("--signoff")
    try:
        # NEW-14: inject PRE_COMMIT_ALLOW_NO_CONFIG=1 so a config-less worktree's
        # pre-commit hook does not abort this recovery commit.
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=_git_commit_env(),
        )
    except (OSError, FileNotFoundError) as e:
        return False, str(e), ""
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return False, stdout.decode("utf-8", errors="replace")[:200], ""

    # Capture the resulting HEAD sha.
    try:
        sha_proc = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(worktree),
            "rev-parse",
            "HEAD",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (OSError, FileNotFoundError):
        return True, "", ""
    sha_stdout, _ = await sha_proc.communicate()
    sha = sha_stdout.decode("utf-8", errors="replace").strip()
    return True, "", sha


async def recover_uncommitted(
    worktree: Path, policy: Stage5CompletenessPolicy
) -> Stage5RecoveryResult:
    """Stage + commit any uncommitted residue in `worktree`. No-op on clean tree."""
    paths = await _git_status_porcelain(worktree)
    if not paths:
        return Stage5RecoveryResult(recovered=False)

    ok, err = await _git_add_all(worktree)
    if not ok:
        return Stage5RecoveryResult(recovered=False, error=f"git add: {err}")

    ok, err, sha = await _git_commit(worktree, policy.commit_marker, policy.signoff)
    if not ok:
        return Stage5RecoveryResult(recovered=False, error=f"git commit: {err}")

    return Stage5RecoveryResult(
        recovered=True,
        commit_sha=sha,
        staged_paths=tuple(paths),
    )


async def stage5_completeness_subscriber(
    event: Event, bus: EventLoop, policy_path: Path | None = None
) -> None:
    """Patch S subscriber — see module docstring.

    Idempotent on non-WORKER_COMPLETED events and on already-halted payloads.
    Wired FIRST in ``_run_real_pilot`` so recovery lands before any halt-gate
    inspects the worktree.
    """
    if event.type != EventType.WORKER_COMPLETED:
        return
    payload = event.payload or {}
    if payload.get("status") != "success":
        return

    worktree_raw = payload.get("worktree")
    story_id = payload.get("story_id") or ""
    if not isinstance(worktree_raw, str) or not worktree_raw:
        log.warning("stage5_skip_missing_worktree", payload=payload)
        return
    worktree = Path(worktree_raw)

    try:
        policy = load_stage5_completeness_policy(policy_path)
    except (PolicyNotFoundError, PolicyInvalidError) as e:
        log.warning("stage5_policy_load_failed", error=str(e))
        return

    if not policy.enabled:
        return

    result = await recover_uncommitted(worktree, policy)
    if not result.recovered:
        if result.error:
            log.warning(
                "stage5_recovery_failed",
                story_id=str(story_id),
                worktree=str(worktree),
                error=result.error,
            )
        return

    log.info(
        "stage5_recovery_commit",
        story_id=str(story_id),
        worktree=str(worktree),
        commit_sha=result.commit_sha,
        staged=list(result.staged_paths),
    )
    payload["stage5_recovery_commit_sha"] = result.commit_sha
    payload["stage5_recovery_paths"] = list(result.staged_paths)


__all__ = [
    "STAGE5_COMPLETENESS_POLICY_PATH_DEFAULT",
    "Stage5CompletenessPolicy",
    "Stage5RecoveryResult",
    "load_stage5_completeness_policy",
    "recover_uncommitted",
    "stage5_completeness_subscriber",
]
