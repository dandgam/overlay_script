"""P4 regression tests — canonical_patches_port (Patch W — File List scope).

Spec: spec/spec_canonical_patches_port.md §P4.
Tracker: .claude/initiative-tracker-canonical_patches_port.md.

Coverage (15 tests):

* **File List parser** (5) — happy path with NEW+UPDATE, missing file,
  empty section, mixed bullet markers + backticks, trailing annotations.
* **AllowList composition** (3) — story file + always-in-scope paths
  always present; retrospective glob match; ``contains`` semantics.
* **Diff size gate scope check** (3) — out-of-scope paths trip
  ``scope_violation``; allow-list happy path passes; per-file partition
  produces correct in-scope totals.
* **Patch R recovery with allow-list** (4) — in-scope only staged,
  out-of-scope left in tree, fully-out-of-scope returns clean+report,
  None allow-list preserves P3 ``add -A`` behaviour.

Reference: ~/.claude/projects/-home-server-odyssey/memory/
skill_improvement_patch_W_candidate.md.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bmad_orchestrator.runtime.commit_recovery import recover_pre_merge
from bmad_orchestrator.runtime.diff_size_gate import (
    DiffSizeGatePolicy,
    DiffSizeMetrics,
    FileDiff,
    gate_verdict,
    measure_diff_per_file,
    partition_per_file,
)
from bmad_orchestrator.runtime.file_list_parser import (
    ALWAYS_IN_SCOPE_PATHS,
    AllowList,
    collect_allow_list,
    has_explicit_file_list,
    parse_file_list,
)

# ───────────────────── helpers ──────────────────────────────────────────────


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _init_git(tmp: Path) -> Path:
    tmp.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(tmp)], check=True)
    subprocess.run(["git", "-C", str(tmp), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(tmp), "config", "user.name", "t"], check=True)
    (tmp / "README").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp), "commit", "-q", "-m", "seed"], check=True)
    return tmp


def _git_status_porcelain(worktree: Path) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(worktree), "status", "--porcelain"],
        check=True, capture_output=True, text=True,
    ).stdout
    return [line[3:].strip() for line in out.splitlines() if line.strip()]


def _git_add_and_commit(worktree: Path, msg: str) -> None:
    """Sync helper — keeps subprocess off the async test body (ruff ASYNC221)."""
    subprocess.run(["git", "-C", str(worktree), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(worktree), "commit", "-q", "-m", msg], check=True
    )


# ───────────────── File List parser ──────────────────────────────────────────


def test_parse_file_list_happy_path(tmp_path: Path) -> None:
    """NEW + UPDATE buckets are recognised and merged into ``all``."""
    story = _write(
        tmp_path / "3.3.md",
        "# Story 3.3\n"
        "\n"
        "Some prose.\n"
        "\n"
        "### File List\n"
        "\n"
        "NEW:\n"
        "- `apps/api/src/foo.rs`\n"
        "- `tests/test_foo.rs`\n"
        "\n"
        "UPDATE:\n"
        "- `docs/known-limitations.md`\n"
        "\n"
        "### Review Findings\n"
        "(other section)\n",
    )
    listing = parse_file_list(story)
    assert listing.new == ("apps/api/src/foo.rs", "tests/test_foo.rs")
    assert listing.update == ("docs/known-limitations.md",)
    assert listing.all == (
        "apps/api/src/foo.rs",
        "tests/test_foo.rs",
        "docs/known-limitations.md",
    )


def test_parse_file_list_missing_file_returns_empty(tmp_path: Path) -> None:
    listing = parse_file_list(tmp_path / "absent.md")
    assert listing.new == ()
    assert listing.update == ()
    assert listing.all == ()


def test_parse_file_list_empty_section(tmp_path: Path) -> None:
    """A story with the heading but no bullets parses to empty FileList."""
    story = _write(
        tmp_path / "s.md",
        "### File List\n\n### Next Heading\n- ignored\n",
    )
    listing = parse_file_list(story)
    assert listing.all == ()


def test_has_explicit_file_list_false_when_missing(tmp_path: Path) -> None:
    """No story file → no explicit File List (permissive)."""
    target = tmp_path / "target"
    (target / "_bmad" / "stories").mkdir(parents=True)
    assert has_explicit_file_list(target, "1.1") is False


def test_has_explicit_file_list_false_when_empty_section(tmp_path: Path) -> None:
    """Story file with empty ### File List → permissive (BMad v6+ pattern)."""
    target = tmp_path / "target"
    story = target / "_bmad" / "stories" / "1.1.md"
    _write(story, "## Dev Agent Record\n\n### File List\n")
    assert has_explicit_file_list(target, "1.1") is False


def test_has_explicit_file_list_true_when_bullets_present(tmp_path: Path) -> None:
    """Story with at least one File List bullet → strict allow-list mode."""
    target = tmp_path / "target"
    story = target / "_bmad" / "stories" / "1.1.md"
    _write(
        story,
        "### File List\n- src/foo.py\n- src/bar.py\n",
    )
    assert has_explicit_file_list(target, "1.1") is True


def test_parse_file_list_mixed_markers_and_backticks(tmp_path: Path) -> None:
    """Bullets may be ``-`` or ``*``; backticks are stripped from paths."""
    story = _write(
        tmp_path / "s.md",
        "### File List\n"
        "* `apps/api/src/a.rs`\n"
        "- apps/api/src/b.rs\n"
        "- `apps/api/src/c.rs` (this file)\n",
    )
    listing = parse_file_list(story)
    assert "apps/api/src/a.rs" in listing.all
    assert "apps/api/src/b.rs" in listing.all
    assert "apps/api/src/c.rs" in listing.all
    # No path has the annotation noise.
    for p in listing.all:
        assert "(" not in p


def test_parse_file_list_trailing_annotations_stripped(tmp_path: Path) -> None:
    """`(this file)`, `(deferred)`, etc. don't pollute the path."""
    story = _write(
        tmp_path / "s.md",
        "### File List\n- `_bmad/stories/s.md` (this file)\n- foo.md (deferred)\n",
    )
    listing = parse_file_list(story)
    assert "_bmad/stories/s.md" in listing.all
    assert "foo.md" in listing.all


# ───────────────── AllowList composition ─────────────────────────────────────


def test_allow_list_includes_always_in_scope_and_story_path(tmp_path: Path) -> None:
    """Allow-list always carries sprint-status, deferred-work, and the story."""
    proj = tmp_path / "proj"
    _write(
        proj / "_bmad" / "stories" / "3.3.md",
        "### File List\n- `apps/api/src/foo.rs`\n",
    )
    allow = collect_allow_list(proj, "3.3")
    assert "apps/api/src/foo.rs" in allow.paths
    assert "_bmad/stories/3.3.md" in allow.paths
    for p in ALWAYS_IN_SCOPE_PATHS:
        assert p in allow.paths


def test_allow_list_glob_matches_wave_retrospective(tmp_path: Path) -> None:
    """Retrospective files match via fnmatch glob, regardless of date suffix."""
    proj = tmp_path / "proj"
    _write(proj / "_bmad" / "stories" / "s.md", "### File List\n")
    allow = collect_allow_list(proj, "s")
    assert allow.contains("_bmad/implementation-artifacts/wave-1a-retrospective.md")
    assert allow.contains("_bmad/implementation-artifacts/retrospective-2026-05-18.md")
    assert not allow.contains("_bmad/implementation-artifacts/wave-1a-retrospective.txt")


def test_allow_list_contains_rejects_unrelated(tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    _write(
        proj / "_bmad" / "stories" / "s.md",
        "### File List\nNEW:\n- foo/in_scope.rs\n",
    )
    allow = collect_allow_list(proj, "s")
    assert allow.contains("foo/in_scope.rs")
    assert not allow.contains("foo/out_of_scope.rs")


# ───────────────── diff_size_gate scope check ───────────────────────────────


def test_gate_verdict_scope_violation_when_out_of_scope_nonempty() -> None:
    """``out_of_scope_paths`` non-empty → reject even if size is tiny."""
    metrics = DiffSizeMetrics(insertions=5, deletions=0, files_changed=1)
    policy = DiffSizeGatePolicy(max_lines=500)
    reason = gate_verdict(
        metrics, policy, out_of_scope_paths=["sibling/test_unrelated.rs"]
    )
    assert reason is not None
    assert "scope_violation" in reason
    assert "sibling/test_unrelated.rs" in reason


def test_gate_verdict_empty_out_of_scope_falls_through_to_size_gate() -> None:
    """Empty out-of-scope list → only the size gate trips (or doesn't)."""
    metrics = DiffSizeMetrics(insertions=10, deletions=5, files_changed=2)
    policy = DiffSizeGatePolicy(max_lines=500)
    assert gate_verdict(metrics, policy, out_of_scope_paths=[]) is None

    big = DiffSizeMetrics(insertions=600, deletions=0, files_changed=3)
    reason = gate_verdict(big, policy, out_of_scope_paths=[])
    assert reason is not None
    assert "diff_size_exceeded" in reason


def test_partition_per_file_splits_by_allow_list() -> None:
    """In-scope files aggregate; out-of-scope paths reported sorted."""
    per_file = [
        FileDiff(path="apps/api/src/foo.rs", insertions=10, deletions=2),
        FileDiff(path="sibling/test_zzz.rs", insertions=50, deletions=0),
        FileDiff(path="_bmad/stories/3.3.md", insertions=1, deletions=0),
        FileDiff(path="sibling/test_aaa.rs", insertions=20, deletions=0),
    ]
    allow = AllowList(
        paths=frozenset({"apps/api/src/foo.rs", "_bmad/stories/3.3.md"}),
    )
    metrics, out = partition_per_file(per_file, allow)
    assert metrics.insertions == 11  # 10 + 1
    assert metrics.deletions == 2
    assert metrics.files_changed == 2
    # Sorted for deterministic diagnostics.
    assert out == ["sibling/test_aaa.rs", "sibling/test_zzz.rs"]


# ───────────────── Patch R recovery with allow-list ─────────────────────────


@pytest.mark.asyncio
async def test_patch_w_recovery_stages_only_in_scope(tmp_path: Path) -> None:
    """Allow-list narrows ``git add`` to in-scope paths only."""
    worktree = _init_git(tmp_path / "wt")
    (worktree / "in_scope.rs").write_text("a=1\n", encoding="utf-8")
    (worktree / "out_of_scope.rs").write_text("b=2\n", encoding="utf-8")
    allow = AllowList(paths=frozenset({"in_scope.rs"}))

    res = await recover_pre_merge(worktree, allow_list=allow)

    assert res.recovered is True
    assert "in_scope.rs" in res.staged_paths
    assert "out_of_scope.rs" not in res.staged_paths
    assert "out_of_scope.rs" in res.out_of_scope_paths
    # The out-of-scope file is still in the worktree, uncommitted.
    porcelain = _git_status_porcelain(worktree)
    assert porcelain == ["out_of_scope.rs"]


@pytest.mark.asyncio
async def test_patch_w_all_dirty_out_of_scope_no_commit(tmp_path: Path) -> None:
    """When every dirty path is out of scope → no commit, paths reported."""
    worktree = _init_git(tmp_path / "wt")
    (worktree / "alien1.txt").write_text("x\n", encoding="utf-8")
    (worktree / "alien2.txt").write_text("y\n", encoding="utf-8")
    allow = AllowList(paths=frozenset({"never_present.md"}))

    res = await recover_pre_merge(worktree, allow_list=allow)

    assert res.recovered is False
    assert res.commit_sha == ""
    assert res.error == ""
    assert set(res.out_of_scope_paths) == {"alien1.txt", "alien2.txt"}
    # Files remain in the working tree (untouched).
    porcelain = _git_status_porcelain(worktree)
    assert set(porcelain) == {"alien1.txt", "alien2.txt"}


@pytest.mark.asyncio
async def test_patch_w_none_allow_list_preserves_p3_behaviour(tmp_path: Path) -> None:
    """``allow_list=None`` → P3 ``git add -A`` semantics retained."""
    worktree = _init_git(tmp_path / "wt")
    (worktree / "a.txt").write_text("1\n", encoding="utf-8")
    (worktree / "b.txt").write_text("2\n", encoding="utf-8")

    res = await recover_pre_merge(worktree, allow_list=None)

    assert res.recovered is True
    assert set(res.staged_paths) >= {"a.txt", "b.txt"}
    assert res.out_of_scope_paths == ()
    # Tree clean after recovery.
    assert _git_status_porcelain(worktree) == []


@pytest.mark.asyncio
async def test_patch_w_measure_diff_per_file_against_real_repo(
    tmp_path: Path,
) -> None:
    """End-to-end: real git diff → per-file rows → partition → gate verdict."""
    worktree = _init_git(tmp_path / "wt")
    (worktree / "in_scope.rs").write_text("a\nb\nc\n", encoding="utf-8")
    (worktree / "alien.rs").write_text("x\n", encoding="utf-8")
    _git_add_and_commit(worktree, "two files")

    per_file = await measure_diff_per_file(worktree)
    paths = {fd.path for fd in per_file}
    assert paths == {"in_scope.rs", "alien.rs"}

    allow = AllowList(paths=frozenset({"in_scope.rs"}))
    metrics, out_of_scope = partition_per_file(per_file, allow)
    assert out_of_scope == ["alien.rs"]
    assert metrics.insertions == 3

    policy = DiffSizeGatePolicy(max_lines=500)
    reason = gate_verdict(metrics, policy, out_of_scope_paths=out_of_scope)
    assert reason is not None
    assert "scope_violation" in reason
    assert "alien.rs" in reason
