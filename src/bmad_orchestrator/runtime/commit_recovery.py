"""Patch R + Patch W — pre-merge commit recovery (canonical port).

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

**Patch W (2026-05-18 — P4)** narrows the recovery's blast radius. Without
it, ``git add -A`` swept the entire worktree including scope-creep from
sibling stories (Odyssey Story 3.3 lost a manual override to exactly this
pattern). When the caller supplies an ``AllowList``, recovery stages ONLY
files inside the allow-list (story File List ∪ infra paths), and reports
out-of-scope paths in the result so the caller can escalate them through
Patch Q's scope check instead of silently committing.

Reference: ~/.claude/skills/bmad-auto-dev/scripts/bmad-auto-dev-runner.sh
``Patch R`` (lines ~821-848 of the original runner) +
``~/.claude/projects/-home-server-odyssey/memory/
skill_improvement_patch_W_candidate.md``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import structlog

from bmad_orchestrator.runtime.file_list_parser import AllowList, partition_paths

log = structlog.get_logger(__name__)

DEFAULT_COMMIT_MARKER = (
    "Patch R recovery: auto-stage Stage 6.retry residue before integration merge"
)


@dataclass(slots=True, frozen=True)
class CommitRecoveryResult:
    recovered: bool
    commit_sha: str = ""
    staged_paths: tuple[str, ...] = ()
    out_of_scope_paths: tuple[str, ...] = ()
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


async def _git_add_paths(worktree: Path, paths: list[str]) -> tuple[bool, str]:
    """Stage explicit pathspecs (Patch W scope-narrowed recovery).

    Each path is staged with an explicit ``git add -- <path>`` invocation so
    glob characters in the path itself are treated literally and untracked
    files are honoured (``git add`` without ``-A`` still picks up untracked
    files when named explicitly).
    """
    if not paths:
        return True, ""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(worktree),
            "add",
            "--",
            *paths,
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
    worktree: Path,
    marker: str = DEFAULT_COMMIT_MARKER,
    signoff: bool = True,
    allow_list: AllowList | None = None,
) -> CommitRecoveryResult:
    """Stage + commit any uncommitted residue in worktree before merge.

    When ``allow_list`` is provided (Patch W), only paths inside the
    allow-list are staged; out-of-scope paths are reported in
    ``result.out_of_scope_paths`` and left in the working tree for the
    caller to surface via Patch Q's scope check + HUMAN_QUERY escalation.
    When the allow-list is exhaustive (every dirty file is out of scope),
    ``recovered`` stays False — nothing was committed — but the result
    still carries the ``out_of_scope_paths`` payload.

    ``allow_list=None`` preserves the P3 behaviour (``git add -A``).

    Returns a result describing the recovery; ``recovered=False`` either
    because the tree was clean / fully out-of-scope (no error set) or a
    git command failed (``error`` populated). Callers proceed with the
    merge in any case — the recovery is best-effort and never blocks it.
    """
    paths = await _git_status_porcelain(worktree)
    if not paths:
        return CommitRecoveryResult(recovered=False)

    if allow_list is not None:
        in_scope, out_of_scope = partition_paths(paths, allow_list)
        if not in_scope:
            log.info(
                "patch_w_all_paths_out_of_scope",
                worktree=str(worktree),
                out_of_scope=out_of_scope,
            )
            return CommitRecoveryResult(
                recovered=False,
                out_of_scope_paths=tuple(out_of_scope),
            )
        ok, err = await _git_add_paths(worktree, in_scope)
        if not ok:
            return CommitRecoveryResult(
                recovered=False,
                error=f"git add: {err}",
                out_of_scope_paths=tuple(out_of_scope),
            )
        staged_for_result = in_scope
        out_of_scope_for_result = tuple(out_of_scope)
    else:
        ok, err = await _git_add_all(worktree)
        if not ok:
            return CommitRecoveryResult(recovered=False, error=f"git add: {err}")
        staged_for_result = paths
        out_of_scope_for_result = ()

    ok, err, sha = await _git_commit(worktree, marker, signoff=signoff)
    if not ok:
        return CommitRecoveryResult(
            recovered=False,
            error=f"git commit: {err}",
            out_of_scope_paths=out_of_scope_for_result,
        )

    return CommitRecoveryResult(
        recovered=True,
        commit_sha=sha,
        staged_paths=tuple(staged_for_result),
        out_of_scope_paths=out_of_scope_for_result,
    )


__all__ = [
    "DEFAULT_COMMIT_MARKER",
    "CommitRecoveryResult",
    "recover_pre_merge",
]
