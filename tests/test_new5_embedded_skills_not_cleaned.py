"""#5 NEW-5 recheck (spec_pilot_findings_closure_v4 §3) — embedded skills are
not dirty residue.

NEW-5 closed the dirty reused-worktree gate in v3. But pilot run #3 story 1.3
landed 0 commits: ``apply_embedded_skills`` writes ~73 files into
``<worktree>/.claude/skills/``, which makes ``git status`` report the worktree
dirty *before* Stage 0. The gate then either halts the story (safe mode) or
``git clean``s the freshly-injected skills away.

The fix: the dirty detector ignores ``.claude/`` paths (embedded skills are an
intentional orchestrator inject), and ``_clean_dirty_worktree`` excludes
``.claude`` from ``git clean`` so real dirt is still discarded without
collateral damage to the skills.

Coverage:
  * 3 unit  — :func:`filter_dirty_outside_claude` drops ``.claude/`` entries,
    keeps real dirt, handles renames/mixed listings.
  * 2 integration — a worktree dirty only with embedded skills does NOT halt
    and the skills survive; real dirt alongside ``.claude/`` is still cleaned.

Spec target: +5 tests.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from bmad_orchestrator.agent.tools._common import worker_jsonl_path
from bmad_orchestrator.runtime.worker_spawn import (
    filter_dirty_outside_claude,
    spawn_worker,
)

# ── unit — filter_dirty_outside_claude ──────────────────────────────────────


def test_filter_drops_claude_entries() -> None:
    """``.claude/`` untracked entries are not treated as dirt."""
    porcelain = [
        "?? .claude/",
        "?? .claude/skills/bmad-auto-dev/SKILL.md",
        " M .claude/settings.local.json",
    ]
    assert filter_dirty_outside_claude(porcelain) == []


def test_filter_keeps_real_dirt() -> None:
    """Dirt outside ``.claude/`` is still surfaced so the gate keeps firing."""
    porcelain = [
        "?? residue.txt",
        " M src/app.py",
        "?? _bmad/auto-dev-state/halt.txt",
    ]
    assert filter_dirty_outside_claude(porcelain) == porcelain


def test_filter_mixed_listing_with_rename() -> None:
    """Mixed listing: ``.claude/`` dropped, real dirt kept, rename uses new path."""
    porcelain = [
        "?? .claude/skills/x/SKILL.md",
        " M README.md",
        "R  old/name.py -> .claude/moved.py",  # renamed INTO .claude → dropped
        "R  a.py -> b.py",                      # rename outside .claude → kept
    ]
    assert filter_dirty_outside_claude(porcelain) == [
        " M README.md",
        "R  a.py -> b.py",
    ]


# ── integration helpers ─────────────────────────────────────────────────────


def _git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "t@t"], check=True
    )
    subprocess.run(["git", "-C", str(path), "config", "user.name", "t"], check=True)
    (path / "README").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "README"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "seed"], check=True)


def _embed_skills(path: Path, n: int = 5) -> list[Path]:
    """Drop ``n`` untracked files under ``.claude/skills/`` — embedded inject."""
    skills = path / ".claude" / "skills" / "bmad-auto-dev"
    skills.mkdir(parents=True, exist_ok=True)
    files: list[Path] = []
    for i in range(n):
        f = skills / f"file{i}.md"
        f.write_text(f"embedded skill {i}\n", encoding="utf-8")
        files.append(f)
    return files


def _read_events(jsonl: Path) -> list[dict[str, object]]:
    if not jsonl.exists():
        return []
    return [
        json.loads(ln)
        for ln in jsonl.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]


def _clear_jsonl(worktree: Path) -> None:
    pre = worker_jsonl_path(str(worktree))
    if pre.exists():
        pre.write_text("", encoding="utf-8")


@pytest.mark.asyncio
async def test_embedded_skills_only_does_not_halt(tmp_path: Path) -> None:
    """Worktree dirty ONLY with embedded skills → safe mode does NOT halt."""
    wt = tmp_path / "wt"
    _git_repo(wt)
    skill_files = _embed_skills(wt)
    _clear_jsonl(wt)

    # Safe mode (auto_clean=False) would halt on any dirt — but embedded
    # skills must not count as dirt.
    handle = await spawn_worker(
        worktree=str(wt),
        story_id="1.3",
        branch="feature/1-3",
        mock=True,
        auto_clean_dirty_worktree=False,
    )

    assert handle.story_id == "1.3"
    # Embedded skills survive — the gate never touched them.
    for f in skill_files:
        assert f.exists(), f"embedded skill {f} must survive the gate"
    events = _read_events(worker_jsonl_path(str(wt)))
    assert not any(e.get("event_type") == "worker_halt_prespawn" for e in events)


@pytest.mark.asyncio
async def test_real_dirt_cleaned_embedded_skills_preserved(tmp_path: Path) -> None:
    """auto_clean=True → real dirt discarded, embedded skills preserved."""
    wt = tmp_path / "wt"
    _git_repo(wt)
    skill_files = _embed_skills(wt)
    (wt / "residue.txt").write_text("leftover from aborted run\n", encoding="utf-8")
    _clear_jsonl(wt)

    handle = await spawn_worker(
        worktree=str(wt),
        story_id="1.3",
        branch="feature/1-3",
        mock=True,
    )

    assert handle.story_id == "1.3"
    assert not (wt / "residue.txt").exists(), "real dirt must be cleaned"
    for f in skill_files:
        assert f.exists(), "git clean -e .claude must preserve embedded skills"
