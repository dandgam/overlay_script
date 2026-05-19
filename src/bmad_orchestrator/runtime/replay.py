"""NEW-19 (spec_pilot_findings_closure_v6 §1) — replay-from-worktree mode.

Validating merge-gate / stage5 / metrics fixes does NOT need the worker-dev
phase — the single most expensive step (~30 min of ``claude -p``). This module
takes an existing git worktree that already carries a dev commit and lets the
post-dev tail of the pipeline run against it (stage5 → build-check →
merge-gate → reconcile → merge), with zero ``spawn_worker`` calls.

Only pure git helpers live here. The bus orchestration lives in
:func:`agent.run.run_replay`, which imports these helpers — keeping this module
free of any back-import of ``agent.run`` (which would be circular, since
``agent.run`` imports heavily from ``runtime``).
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)


class ReplayError(RuntimeError):
    """Raised when a worktree cannot be prepared for a replay run."""


@dataclass(slots=True, frozen=True)
class ReplayWorktree:
    """Resolved replay metadata for one worktree.

    ``base_sha`` is the fork point of the worktree's HEAD vs the integration
    branch; ``dev_commits`` are the SHAs on HEAD past ``base_sha`` (oldest
    first) — the work the post-dev pipeline tail will try to merge.
    ``synthesized`` is True when ``--auto-commit-dev`` turned a dirty worktree
    into a dev commit.
    """

    path: Path
    story_id: str
    integration_branch: str
    base_sha: str
    dev_commits: tuple[str, ...]
    synthesized: bool = False

    @property
    def has_dev_work(self) -> bool:
        """True when the worktree carries at least one dev commit to replay."""
        return bool(self.dev_commits)


async def _git(
    repo: Path, *args: str, env: dict[str, str] | None = None
) -> tuple[int, str, str]:
    """Run ``git -C <repo> <args>``; return (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        "git", "-C", str(repo), *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    out, err = await proc.communicate()
    return (
        proc.returncode or 0,
        out.decode(errors="replace").strip(),
        err.decode(errors="replace").strip(),
    )


def _git_commit_env() -> dict[str, str]:
    """Env for ``git commit`` subprocesses — tolerate config-less worktrees.

    Mirrors the NEW-12 pre-commit escape hatch so a synthesized dev commit in
    a worktree without ``.pre-commit-config.yaml`` never trips the hook.
    """
    env = dict(os.environ)
    env["PRE_COMMIT_ALLOW_NO_CONFIG"] = "1"
    return env


async def worktree_is_dirty(worktree: Path) -> bool:
    """True when the worktree has uncommitted (staged or unstaged) changes."""
    rc, out, err = await _git(worktree, "status", "--porcelain")
    if rc != 0:
        raise ReplayError(f"git status failed in {worktree}: {err}")
    return bool(out)


async def resolve_base_sha(worktree: Path, integration_branch: str) -> str:
    """Fork point of the worktree's HEAD vs the integration branch.

    Falls back to ``main`` and then to the repo's first commit, so a replay on
    a worktree branched off a not-yet-created integration branch still
    resolves a usable base.
    """
    for ref in (integration_branch, "main"):
        rc, out, _ = await _git(worktree, "merge-base", "HEAD", ref)
        if rc == 0 and out:
            return out.splitlines()[0].strip()
    rc, out, _ = await _git(worktree, "rev-list", "--max-parents=0", "HEAD")
    if rc == 0 and out:
        return out.splitlines()[0].strip()
    raise ReplayError(f"cannot resolve base sha in {worktree}")


async def detect_dev_commits(worktree: Path, base_sha: str) -> tuple[str, ...]:
    """SHAs on the worktree's HEAD past ``base_sha`` — oldest first."""
    if not base_sha:
        return ()
    rc, out, _ = await _git(
        worktree, "rev-list", "--reverse", f"{base_sha}..HEAD"
    )
    if rc != 0:
        return ()
    return tuple(line.strip() for line in out.splitlines() if line.strip())


async def synthesize_dev_commit(worktree: Path, story_id: str) -> str:
    """Stage every working change and commit it as a synthetic dev commit.

    Used by ``replay --auto-commit-dev`` to reproduce the post-dev pipeline
    path on a worktree whose work is still uncommitted (e.g. a worker that
    exited dirty — NEW-17 territory). Returns the new HEAD SHA.
    """
    rc, _, err = await _git(worktree, "add", "-A")
    if rc != 0:
        raise ReplayError(f"git add failed in {worktree}: {err}")
    rc, _, err = await _git(
        worktree, "commit", "-m",
        f"replay: synthesized dev commit for {story_id}",
        env=_git_commit_env(),
    )
    if rc != 0:
        raise ReplayError(f"git commit failed in {worktree}: {err}")
    rc, out, err = await _git(worktree, "rev-parse", "HEAD")
    if rc != 0:
        raise ReplayError(f"git rev-parse failed in {worktree}: {err}")
    return out.strip()


async def commit_is_merged(repo: Path, commit_sha: str, branch: str) -> bool:
    """True when ``commit_sha`` is an ancestor of ``branch`` in ``repo``.

    Ground-truth check that a replay actually landed the dev work in the
    integration branch. ``False`` when the branch does not exist.
    """
    if not commit_sha:
        return False
    rc, _, _ = await _git(
        repo, "merge-base", "--is-ancestor", commit_sha, branch
    )
    return rc == 0


async def prepare_replay_worktree(
    *,
    worktree: Path,
    story_id: str,
    integration_branch: str,
    auto_commit_dev: bool = False,
) -> ReplayWorktree:
    """Validate a worktree and resolve its replay metadata.

    * Missing path / not a git worktree → :class:`ReplayError`.
    * Dirty worktree + ``auto_commit_dev`` → synthesize a dev commit so the
      post-dev pipeline tail has a commit to merge.
    * Dirty worktree without the flag → kept as-is (committed commits only);
      a warning is logged so the operator knows uncommitted work is ignored.
    """
    worktree = worktree.resolve()
    if not worktree.exists():
        raise ReplayError(f"worktree path does not exist: {worktree}")
    if not (worktree / ".git").exists():
        raise ReplayError(f"not a git worktree (no .git entry): {worktree}")

    synthesized = False
    if await worktree_is_dirty(worktree):
        if auto_commit_dev:
            sha = await synthesize_dev_commit(worktree, story_id)
            synthesized = True
            log.info(
                "replay_synthesized_dev_commit",
                worktree=str(worktree), story=story_id, sha=sha,
            )
        else:
            log.warning(
                "replay_worktree_dirty_ignored",
                worktree=str(worktree), story=story_id,
                note="uncommitted changes ignored — pass "
                "--auto-commit-dev to include them in the replay",
            )

    base_sha = await resolve_base_sha(worktree, integration_branch)
    dev_commits = await detect_dev_commits(worktree, base_sha)
    return ReplayWorktree(
        path=worktree,
        story_id=story_id,
        integration_branch=integration_branch,
        base_sha=base_sha,
        dev_commits=dev_commits,
        synthesized=synthesized,
    )


__all__ = [
    "ReplayError",
    "ReplayWorktree",
    "commit_is_merged",
    "detect_dev_commits",
    "prepare_replay_worktree",
    "resolve_base_sha",
    "synthesize_dev_commit",
    "worktree_is_dirty",
]
