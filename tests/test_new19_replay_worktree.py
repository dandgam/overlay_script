"""NEW-19 (spec_pilot_findings_closure_v6 §1) — replay-from-worktree mode.

Replay drives the post-dev pipeline tail (stage5 → build-check → merge-gate →
reconcile → merge) against an existing worktree that already carries a dev
commit, with zero ``spawn_worker`` calls. It exists so merge-gate / stage5 /
metrics fixes can be validated in seconds instead of a ~30-min worker-dev run.

Each test reproduces the concrete code path it guards (v6 §0 rule):

  * unit ×3 — CLI flag parsing, the spawn_worker-skip path, dev-commit
    detection in a real git worktree;
  * integration ×2 — a mock worktree with a ready dev commit replays the tail
    without a spawn; a dirty worktree + ``--auto-commit-dev`` synthesizes the
    dev commit before replaying.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bmad_orchestrator.agent import run
from bmad_orchestrator.cli.main import app
from bmad_orchestrator.config import load_settings
from bmad_orchestrator.runtime.event_loop import Event, EventLoop, EventType
from bmad_orchestrator.runtime.replay import (
    detect_dev_commits,
    prepare_replay_worktree,
    resolve_base_sha,
)

# ── git fixtures ─────────────────────────────────────────────────────────────


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def _init_repo(repo: Path) -> str:
    """Create a git repo with one main commit; return its SHA."""
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@virgil.local")
    _git(repo, "config", "user.name", "Virgil Test")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base commit")
    return _git(repo, "rev-parse", "HEAD")


def _make_worktree_with_dev_commit(repo: Path, story_id: str) -> tuple[str, str]:
    """Branch feature/<story>, add a dev commit, create integration/1a at base.

    Returns (base_sha, dev_sha). The repo dir itself doubles as the worktree
    (it has a ``.git`` entry — enough for the replay validation).
    """
    base_sha = _init_repo(repo)
    _git(repo, "branch", "integration/1a", base_sha)
    _git(repo, "checkout", "-q", "-b", f"feature/{story_id}")
    (repo / "feature.py").write_text("# dev work\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", f"feat: story {story_id}")
    dev_sha = _git(repo, "rev-parse", "HEAD")
    return base_sha, dev_sha


def _settings_for(repo: Path):  # type: ignore[no-untyped-def]
    return load_settings().model_copy(update={"target_project": repo})


# ── stub subscribers (mock the post-dev gate chain) ──────────────────────────


def _register_stub_gate(bus: EventLoop, repo: Path) -> list[EventType]:
    """Register a minimal WORKER_COMPLETED → approve → ff-merge chain.

    Mirrors the real Phase-4 tail without spawning Opus reviewers: on success
    it emits an approve verdict; on the verdict it fast-forwards the feature
    branch into integration/1a. Returns a list that records dispatched types.
    """
    seen: list[EventType] = []

    async def verdict_stub(event: Event) -> None:
        seen.append(event.type)
        if event.type == EventType.WORKER_COMPLETED:
            await bus.emit(
                EventType.CODE_REVIEW_VERDICT,
                story_id=event.payload.get("story_id"),
                verdict="approve",
                worktree=event.payload.get("worktree"),
            )

    async def merge_stub(event: Event) -> None:
        if event.type == EventType.CODE_REVIEW_VERDICT and (
            event.payload.get("verdict") == "approve"
        ):
            sid = event.payload.get("story_id")
            _git(repo, "branch", "-f", "integration/1a", f"feature/{sid}")

    bus.on(verdict_stub)
    bus.on(merge_stub)
    return seen


# ══ unit ═════════════════════════════════════════════════════════════════════


def test_replay_cli_flag_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    """`replay` parses --worktree/--story/--integration and forwards them."""
    captured: dict[str, object] = {}

    async def fake_run_replay(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"story_merged": True, "verdict": "approve",
                "dev_commits": ["abc"], "synthesized": False,
                "merge_skipped": False}

    monkeypatch.setattr(run, "run_replay", fake_run_replay)

    result = CliRunner().invoke(
        app,
        ["replay", "--worktree", "/tmp/wt-1.4", "--story", "1.4",
         "--integration", "integration/1a"],
    )

    assert result.exit_code == 0, result.output
    assert captured["story_id"] == "1.4"
    assert captured["integration_branch"] == "integration/1a"
    assert captured["worktree"] == Path("/tmp/wt-1.4")
    assert captured["auto_commit_dev"] is False


@pytest.mark.asyncio
async def test_replay_skips_spawn_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run_replay drives the tail without ever calling spawn_worker."""
    repo = tmp_path / "proj"
    _make_worktree_with_dev_commit(repo, "1.4")

    async def boom(*args: object, **kwargs: object) -> object:
        raise AssertionError("spawn_worker must not be called in replay mode")

    monkeypatch.setattr(run, "runtime_spawn_worker", boom)

    bus = EventLoop()
    _register_stub_gate(bus, repo)
    result = await run.run_replay(
        worktree=repo, story_id="1.4", integration_branch="integration/1a",
        settings=_settings_for(repo), bus=bus, wire_subscribers=False,
    )

    assert result["spawned_worker"] is False
    assert result["verdict"] == "approve"


@pytest.mark.asyncio
async def test_detect_dev_commits_in_worktree(tmp_path: Path) -> None:
    """detect_dev_commits + resolve_base_sha find the dev work past base."""
    repo = tmp_path / "proj"
    base_sha, dev_sha = _make_worktree_with_dev_commit(repo, "1.4")

    resolved_base = await resolve_base_sha(repo, "integration/1a")
    assert resolved_base == base_sha

    commits = await detect_dev_commits(repo, resolved_base)
    assert len(commits) == 1
    assert commits[0] == dev_sha

    # No commits past HEAD itself.
    assert await detect_dev_commits(repo, dev_sha) == ()


# ══ integration ══════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_replay_runs_tail_for_ready_worktree(tmp_path: Path) -> None:
    """Mock worktree with a ready dev commit → replay merges it, no spawn."""
    repo = tmp_path / "proj"
    _base, dev_sha = _make_worktree_with_dev_commit(repo, "1.4")

    bus = EventLoop()
    seen = _register_stub_gate(bus, repo)

    result = await run.run_replay(
        worktree=repo, story_id="1.4", integration_branch="integration/1a",
        settings=_settings_for(repo), bus=bus, wire_subscribers=False,
    )

    # Pipeline tail ran: WORKER_COMPLETED reached the gate chain.
    assert EventType.WORKER_COMPLETED in seen
    assert result["story_merged"] is True
    assert result["dev_commits"] == [dev_sha]
    assert result["synthesized"] is False
    assert result["merge_skipped"] is False
    # Ground truth — dev commit is now on integration/1a.
    assert _git(repo, "rev-parse", "integration/1a") == dev_sha


@pytest.mark.asyncio
async def test_replay_auto_commit_dev_synthesizes_commit(
    tmp_path: Path,
) -> None:
    """Dirty worktree + --auto-commit-dev → synthesized dev commit, then merge."""
    repo = tmp_path / "proj"
    base_sha = _init_repo(repo)
    _git(repo, "branch", "integration/1a", base_sha)
    _git(repo, "checkout", "-q", "-b", "feature/1.5")
    # Uncommitted working change — the NEW-17 "worker exited dirty" shape.
    (repo / "dirty.py").write_text("# uncommitted dev work\n", encoding="utf-8")

    bus = EventLoop()
    _register_stub_gate(bus, repo)

    result = await run.run_replay(
        worktree=repo, story_id="1.5", integration_branch="integration/1a",
        settings=_settings_for(repo), bus=bus, wire_subscribers=False,
        auto_commit_dev=True,
    )

    assert result["synthesized"] is True
    assert len(result["dev_commits"]) == 1
    assert result["story_merged"] is True
    # The synthesized commit landed on integration/1a.
    head = _git(repo, "rev-parse", "HEAD")
    assert _git(repo, "rev-parse", "integration/1a") == head

    # Sanity — prepare_replay_worktree without the flag leaves dirt uncommitted.
    repo2 = tmp_path / "proj2"
    _init_repo(repo2)
    _git(repo2, "branch", "integration/1a", "HEAD")
    (repo2 / "dirty.py").write_text("# still uncommitted\n", encoding="utf-8")
    rw = await prepare_replay_worktree(
        worktree=repo2, story_id="1.6", integration_branch="integration/1a",
        auto_commit_dev=False,
    )
    assert rw.synthesized is False
