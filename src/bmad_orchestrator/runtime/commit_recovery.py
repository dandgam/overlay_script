"""Patch R — pre-merge commit recovery (canonical port from runner.sh).

Helper used by ``merge_to_integration_subscriber``. Just before the ff-merge
of ``feature/<story>`` into ``integration/<wave>``, if the worker's worktree
has uncommitted residue (anything ``git status --porcelain`` reports), stage
+ commit it with a marker message so the merge is fast-forwardable and no
work is lost.

This is defensively redundant with Patch S (which also auto-commits residue,
but earlier — right after WORKER_COMPLETED). Patch R catches anything that
appeared between Stage 5 and the merge — e.g. a code-review auto-fix step
that touched files but didn't commit, or a hook that fires on the merge
side. The bash runner historically lost work because of this gap; Patch R
closes it.

Reference: ~/.claude/skills/bmad-auto-dev/scripts/bmad-auto-dev-runner.sh
``Patch R`` (lines ~821-848 of the original runner).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

DEFAULT_COMMIT_MARKER = (
    "Patch R recovery: auto-stage Stage 6.retry residue before integration merge"
)


@dataclass(slots=True, frozen=True)
class CommitRecoveryResult:
    recovered: bool
    commit_sha: str = ""
    staged_paths: tuple[str, ...] = ()
    error: str = ""


async def _git_status_porcelain(worktree: Path) -> list[str]:
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
        log.warning("patch_r_git_status_spawn_failed", worktree=str(worktree), error=str(e))
        return []
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        log.info(
            "patch_r_git_status_nonzero",
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
    worktree: Path, message: str, signoff: bool = True
) -> tuple[bool, str, str]:
    args = ["git", "-C", str(worktree), "commit", "-m", message]
    if signoff:
        args.append("--signoff")
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except (OSError, FileNotFoundError) as e:
        return False, str(e), ""
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return False, stdout.decode("utf-8", errors="replace")[:200], ""
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
    return True, "", sha_stdout.decode("utf-8", errors="replace").strip()


async def recover_pre_merge(
    worktree: Path, marker: str = DEFAULT_COMMIT_MARKER, signoff: bool = True
) -> CommitRecoveryResult:
    """Stage + commit any uncommitted residue in worktree before merge.

    Returns a result describing the recovery; ``recovered=False`` either
    because the tree was clean (no error set) or the git command failed
    (``error`` populated). Callers proceed with the merge in both cases —
    the recovery is best-effort and never blocks the merge itself.
    """
    paths = await _git_status_porcelain(worktree)
    if not paths:
        return CommitRecoveryResult(recovered=False)

    ok, err = await _git_add_all(worktree)
    if not ok:
        return CommitRecoveryResult(recovered=False, error=f"git add: {err}")

    ok, err, sha = await _git_commit(worktree, marker, signoff=signoff)
    if not ok:
        return CommitRecoveryResult(recovered=False, error=f"git commit: {err}")

    return CommitRecoveryResult(
        recovered=True,
        commit_sha=sha,
        staged_paths=tuple(paths),
    )


__all__ = [
    "DEFAULT_COMMIT_MARKER",
    "CommitRecoveryResult",
    "recover_pre_merge",
]
