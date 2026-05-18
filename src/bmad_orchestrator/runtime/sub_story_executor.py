"""Sub-story execution + squash-merge (Initiative #2 Task 2.3 + 2.4).

After Initiative #2 Task 2.2's LLM decomposer emits a validated list of
sub-stories, this module is the *executor* leg: each sub-story is dispatched
sequentially inside the **shared parent worktree** (KISS per spec §Task 2.3 —
sub-story parallelism is a separate initiative), and the resulting commits
are squash-merged into a single parent commit on the parent feature branch
(spec §Task 2.4).

Why a shared parent worktree (vs per-sub-story worktrees):
* setup cost is amortised — one ``_ensure_git_worktree`` for the parent
  story instead of N for the subs;
* sub-stories observe each other's commits naturally (sequential semantic);
* the eventual squash collapses N commits back to one, mirroring how the
  story was authored originally;
* sub-story-level parallelism would re-introduce the file-conflict bookkeeping
  Initiative #1 already solved at the *story* level — that work is deferred
  to its own spec.

Decoupling:
* No direct EventBus dependency — callers pass an optional ``on_event``
  callback (one ``dict`` per phase). Keeps this module trivially testable
  in unit tests without spinning the full event loop.
* ``spawn_fn`` / ``wait_fn`` are injection points; defaults call into
  :mod:`bmad_orchestrator.runtime.worker_spawn`. Tests pass mock shims.
* Git operations are local subprocess calls (``git reset --soft`` +
  ``git commit``) — no GitPython dep, no async file I/O.
"""

from __future__ import annotations

import subprocess
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bmad_orchestrator.runtime.worker_spawn import WorkerHandle, spawn_worker

SpawnFn = Callable[..., Awaitable[WorkerHandle]]
WaitFn = Callable[[WorkerHandle], Awaitable[int]]
EventHook = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class SubStoryResult:
    """Outcome of one sub-story's worker run inside the shared parent worktree."""

    sub_id: str
    exit_code: int
    commits_added: int
    head_sha_after: str
    handle: WorkerHandle | None
    failure_reason: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0 and self.failure_reason is None


@dataclass(frozen=True)
class SquashResult:
    """Outcome of squash-merging sub-story commits back to one parent commit."""

    parent_story_id: str
    base_sha: str
    pre_squash_head: str
    squashed_sha: str
    commits_squashed: int
    message: str
    skipped: bool = False


class SubStoryExecutionError(RuntimeError):
    """Sub-story execution detected unrecoverable input or git state failure."""


def _git(args: list[str], cwd: Path) -> str:
    cmd = ["git", *args]
    proc = subprocess.run(  # noqa: S603 — own argv, no shell  # nosec
        cmd, cwd=cwd, check=True, capture_output=True, text=True
    )
    return proc.stdout.strip()


def _head_sha(cwd: Path) -> str:
    return _git(["rev-parse", "HEAD"], cwd)


def _count_commits_between(base_sha: str, head_ref: str, cwd: Path) -> int:
    out = _git(["rev-list", "--count", f"{base_sha}..{head_ref}"], cwd)
    return int(out)


def _current_branch(cwd: Path) -> str:
    return _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)


async def _default_wait(handle: WorkerHandle) -> int:
    """Default wait shim — returns 0 for mock handles (already synthesised),
    otherwise awaits the subprocess.

    The real ``worker_spawn`` already emits ``worker_completed`` into the
    JSONL after the subprocess exits; we only need the numeric exit code here
    to short-circuit halt-on-failure.
    """
    if handle.mock or handle.process is None:
        return 0
    return await handle.process.wait()


async def execute_sub_stories(
    *,
    parent_story_id: str,
    sub_stories: list[dict[str, Any]],
    worktree: Path,
    branch: str,
    base_sha: str,
    spawn_fn: SpawnFn | None = None,
    wait_fn: WaitFn | None = None,
    halt_on_failure: bool = True,
    spawn_kwargs: dict[str, Any] | None = None,
    on_event: EventHook | None = None,
) -> list[SubStoryResult]:
    """Run sub-stories sequentially inside the shared parent worktree.

    Each sub-story dispatch:
      1. Verifies the worktree is on ``branch`` (single-branch invariant);
      2. Calls ``spawn_fn`` with ``story_id=<sub.id>``, ``branch=<parent branch>``,
         and the same ``worktree`` path — worker commits land directly on the
         parent feature branch;
      3. Awaits worker exit via ``wait_fn``;
      4. Counts commits added since the previous head (so silent failures
         — exit 0 + zero commits — are observable per Phase 0 Task 0.2);
      5. Halts the loop if ``halt_on_failure`` and the result is not ``succeeded``.

    Returns one ``SubStoryResult`` per attempted sub-story. The list reflects
    *attempts*: if halt_on_failure stops after sub #3 of 5, only 3 results
    are returned.

    Raises:
      SubStoryExecutionError — malformed input (missing id, mismatched branch,
      worktree not a git repo).
    """
    if not sub_stories:
        return []

    spawn = spawn_fn or spawn_worker
    wait = wait_fn or _default_wait
    kwargs = dict(spawn_kwargs or {})

    if not (worktree / ".git").exists() and not (worktree / ".git").is_file():
        raise SubStoryExecutionError(
            f"worktree {worktree} is not a git repo / worktree linkage"
        )

    head_branch = _current_branch(worktree)
    if head_branch != branch:
        raise SubStoryExecutionError(
            f"worktree on branch {head_branch!r}, expected {branch!r}"
        )

    results: list[SubStoryResult] = []
    last_head = base_sha

    for sub in sub_stories:
        sid = str(sub.get("id") or "").strip()
        if not sid:
            raise SubStoryExecutionError(
                f"sub-story missing 'id': {sub!r}"
            )
        if on_event:
            on_event({
                "event_type": "sub_story_started",
                "parent_story_id": parent_story_id,
                "sub_id": sid,
                "worktree": str(worktree),
                "branch": branch,
            })
        handle = await spawn(
            worktree=str(worktree),
            story_id=sid,
            branch=branch,
            **kwargs,
        )
        exit_code = await wait(handle)
        head_now = _head_sha(worktree)
        commits_added = _count_commits_between(last_head, head_now, worktree)
        failure_reason: str | None = None
        if exit_code != 0:
            failure_reason = f"worker_exit_code={exit_code}"
        elif commits_added == 0:
            failure_reason = "silent_failure_zero_commits"
        result = SubStoryResult(
            sub_id=sid,
            exit_code=exit_code,
            commits_added=commits_added,
            head_sha_after=head_now,
            handle=handle,
            failure_reason=failure_reason,
        )
        results.append(result)
        if on_event:
            on_event({
                "event_type": "sub_story_completed",
                "parent_story_id": parent_story_id,
                "sub_id": sid,
                "exit_code": exit_code,
                "commits_added": commits_added,
                "head_sha_after": head_now,
                "succeeded": result.succeeded,
                "failure_reason": failure_reason,
            })
        last_head = head_now
        if not result.succeeded and halt_on_failure:
            break

    return results


def squash_sub_stories(
    *,
    parent_story_id: str,
    worktree: Path,
    base_sha: str,
    sub_ids: list[str],
    extra_message_lines: list[str] | None = None,
    on_event: EventHook | None = None,
) -> SquashResult:
    """Squash all commits between ``base_sha`` and ``HEAD`` into one parent commit.

    Mechanism: ``git reset --soft <base_sha>`` rewinds the branch pointer while
    keeping the working tree + index untouched, then a fresh ``git commit``
    re-records the cumulative diff under a single message that lists every
    sub-story id.

    Edge cases:
      * **0 commits between base and HEAD** — nothing to squash; returns with
        ``skipped=True`` and ``squashed_sha == pre_squash_head``.
      * **1 commit** — single commit already represents the unit; we return
        ``skipped=True`` and leave the existing commit message alone (caller
        can rewrite via ``git commit --amend`` if they need to enforce the
        parent-message format, but for the squash *invariant* a single
        commit IS the parent commit).
      * **N>=2 commits, no actual file changes** — git refuses ``commit``
        without ``--allow-empty``; we retry with that flag. (Possible when
        all sub-stories were mock workers in a test fixture.)

    Returns the SquashResult; never raises on the success path.
    """
    pre_head = _head_sha(worktree)
    commits = _count_commits_between(base_sha, pre_head, worktree)
    if commits == 0:
        if on_event:
            on_event({
                "event_type": "sub_story_squash_skipped",
                "parent_story_id": parent_story_id,
                "reason": "zero_commits",
            })
        return SquashResult(
            parent_story_id=parent_story_id,
            base_sha=base_sha,
            pre_squash_head=pre_head,
            squashed_sha=pre_head,
            commits_squashed=0,
            message="",
            skipped=True,
        )
    if commits == 1:
        msg = _git(["log", "-1", "--pretty=%B", pre_head], worktree)
        if on_event:
            on_event({
                "event_type": "sub_story_squash_skipped",
                "parent_story_id": parent_story_id,
                "reason": "single_commit",
                "head_sha": pre_head,
            })
        return SquashResult(
            parent_story_id=parent_story_id,
            base_sha=base_sha,
            pre_squash_head=pre_head,
            squashed_sha=pre_head,
            commits_squashed=1,
            message=msg,
            skipped=True,
        )

    title = f"feat({parent_story_id}): squash {len(sub_ids)} sub-stories"
    body_lines = [
        "",
        "Sub-stories:",
        *[f"  - {sid}" for sid in sub_ids],
    ]
    if extra_message_lines:
        body_lines.append("")
        body_lines.extend(extra_message_lines)
    msg = title + "\n" + "\n".join(body_lines) + "\n"

    _git(["reset", "--soft", base_sha], worktree)
    try:
        _git(["commit", "-m", msg], worktree)
    except subprocess.CalledProcessError:
        _git(["commit", "--allow-empty", "-m", msg], worktree)

    squashed_sha = _head_sha(worktree)
    if on_event:
        on_event({
            "event_type": "sub_story_squash_done",
            "parent_story_id": parent_story_id,
            "pre_squash_head": pre_head,
            "squashed_sha": squashed_sha,
            "commits_squashed": commits,
            "sub_ids": list(sub_ids),
        })
    return SquashResult(
        parent_story_id=parent_story_id,
        base_sha=base_sha,
        pre_squash_head=pre_head,
        squashed_sha=squashed_sha,
        commits_squashed=commits,
        message=msg,
    )


__all__ = [
    "EventHook",
    "SpawnFn",
    "SquashResult",
    "SubStoryExecutionError",
    "SubStoryResult",
    "WaitFn",
    "execute_sub_stories",
    "squash_sub_stories",
]
