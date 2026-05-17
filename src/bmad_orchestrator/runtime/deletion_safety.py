"""Patch C — deletion safety subscriber (canonical port from runner.sh).

On ``WORKER_COMPLETED`` (status=success) the subscriber inspects the worker's
last commit diff in its worktree and flags deletions matching patterns in
``skills/policy/deletion-safety.yaml``. Matches halt the event:

  * ``event.payload['status']`` is mutated to ``'halted_unsafe_deletion'`` so
    the downstream ``code_review_subscriber`` (which gates on ``status ==
    'success'``) skips — no $15 Opus run on a destructive worker output.
  * A ``HUMAN_QUERY`` is emitted with the offending paths + escalation text.

Designed to be wired BEFORE ``code_review_subscriber`` in ``_run_real_pilot``.

Reference: ~/.claude/skills/bmad-auto-dev/scripts/bmad-auto-dev-runner.sh
``migration_patterns`` (lines ~566-580 of the original 1100 LOC bash runner).
"""

from __future__ import annotations

import asyncio
import fnmatch
from dataclasses import dataclass
from pathlib import Path

import structlog
import yaml
from pydantic import BaseModel, Field, ValidationError

from bmad_orchestrator.runtime.event_loop import Event, EventLoop, EventType
from bmad_orchestrator.skills_repo import PolicyInvalidError, PolicyNotFoundError

log = structlog.get_logger(__name__)

DELETION_SAFETY_POLICY_PATH_DEFAULT = (
    Path(__file__).resolve().parents[3] / "skills" / "policy" / "deletion-safety.yaml"
)


class DeletionSafetyPolicy(BaseModel):
    """Schema for ``skills/policy/deletion-safety.yaml``."""

    patterns: list[str] = Field(default_factory=list)
    escalation_text: str = ""


@dataclass(slots=True, frozen=True)
class DeletionMatch:
    path: str
    pattern: str


def load_deletion_safety_policy(
    path: Path | None = None,
) -> DeletionSafetyPolicy:
    """Load + validate the deletion-safety policy yaml.

    Raises:
        PolicyNotFoundError — file missing.
        PolicyInvalidError  — YAML parse OR schema validation fail.
    """
    p = path or DELETION_SAFETY_POLICY_PATH_DEFAULT
    if not p.exists():
        raise PolicyNotFoundError(f"deletion-safety policy not found: {p}")
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
        return DeletionSafetyPolicy.model_validate(raw)
    except ValidationError as e:
        raise PolicyInvalidError(
            f"deletion-safety schema validation failed: {e}"
        ) from e


def match_deletions(
    deleted_paths: list[str], policy: DeletionSafetyPolicy
) -> list[DeletionMatch]:
    """Return all (path, pattern) hits — path matches both as basename and full path."""
    hits: list[DeletionMatch] = []
    for raw in deleted_paths:
        path = raw.strip()
        if not path:
            continue
        basename = path.rsplit("/", 1)[-1]
        for pattern in policy.patterns:
            if fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(basename, pattern):
                hits.append(DeletionMatch(path=path, pattern=pattern))
                break
    return hits


async def _git_diff_deleted_paths(worktree: Path) -> list[str]:
    """Return list of deleted file paths in the worker's last commit.

    Uses ``git show --diff-filter=D --name-only --pretty=format: HEAD`` — the
    HEAD commit's deletions only. Returns [] if the worktree has no git history
    yet or git fails (caller treats empty list as "nothing to halt on").
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(worktree),
            "show",
            "--diff-filter=D",
            "--name-only",
            "--pretty=format:",
            "HEAD",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (OSError, FileNotFoundError) as e:
        log.warning("git_diff_spawn_failed", worktree=str(worktree), error=str(e))
        return []
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        log.info(
            "git_diff_nonzero",
            worktree=str(worktree),
            returncode=proc.returncode,
            stderr=stderr.decode("utf-8", errors="replace")[:200],
        )
        return []
    text = stdout.decode("utf-8", errors="replace")
    return [line for line in (ln.strip() for ln in text.splitlines()) if line]


async def deletion_safety_subscriber(
    event: Event, bus: EventLoop, policy_path: Path | None = None
) -> None:
    """Patch C subscriber — see module docstring.

    Idempotent on non-WORKER_COMPLETED events and on already-halted payloads.
    Designed to run BEFORE ``code_review_subscriber`` so a halt skips the
    downstream review spawn entirely.
    """
    if event.type != EventType.WORKER_COMPLETED:
        return
    payload = event.payload or {}
    if payload.get("status") != "success":
        return

    worktree_raw = payload.get("worktree")
    story_id = payload.get("story_id") or ""
    if not isinstance(worktree_raw, str) or not worktree_raw:
        log.warning("deletion_safety_skip_missing_worktree", payload=payload)
        return
    worktree = Path(worktree_raw)

    try:
        policy = load_deletion_safety_policy(policy_path)
    except (PolicyNotFoundError, PolicyInvalidError) as e:
        log.warning("deletion_safety_policy_load_failed", error=str(e))
        return

    deleted = await _git_diff_deleted_paths(worktree)
    if not deleted:
        return

    hits = match_deletions(deleted, policy)
    if not hits:
        return

    unsafe_paths = [h.path for h in hits]
    log.warning(
        "deletion_safety_halt",
        story_id=story_id,
        worktree=str(worktree),
        unsafe_paths=unsafe_paths,
    )

    payload["status"] = "halted_unsafe_deletion"
    payload["halt_reason"] = "deletion_safety"
    payload["unsafe_deletions"] = unsafe_paths

    bullet_list = "\n".join(f"  - {h.path}  (matched: {h.pattern})" for h in hits)
    await bus.emit(
        EventType.HUMAN_QUERY,
        story_id=str(story_id),
        worktree=str(worktree),
        verdict="deletion_safety_halt",
        text=(
            f"{policy.escalation_text}\n\n"
            f"Story: {story_id}\n"
            f"Worktree: {worktree}\n"
            f"Unsafe deletions ({len(hits)}):\n{bullet_list}"
        ),
        unsafe_paths=unsafe_paths,
        actions=["abandon", "manual_review"],
    )


__all__ = [
    "DELETION_SAFETY_POLICY_PATH_DEFAULT",
    "DeletionMatch",
    "DeletionSafetyPolicy",
    "deletion_safety_subscriber",
    "load_deletion_safety_policy",
    "match_deletions",
]
