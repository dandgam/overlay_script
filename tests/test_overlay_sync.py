"""Hermetic tests for tools/overlay_sync.py (Этап 1 + 1.5).

Pure-logic and safety tests use fake file trees in tmp_path. Git-dependent
classification (census head_relation) is exercised by monkeypatching the single
git_show_head boundary, so the suite needs no git config.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "overlay_sync", Path(__file__).resolve().parents[1] / "tools" / "overlay_sync.py"
)
osync = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
sys.modules["overlay_sync"] = osync  # register before exec so dataclasses resolve __module__
_SPEC.loader.exec_module(osync)


# --- fixtures --------------------------------------------------------------


def _write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """root/{odyssey,legal}/_bmad/custom/*.toml + brainstorming steps + upstream."""
    root = tmp_path / "root"
    for proj in ("odyssey", "legal"):
        cdir = root / proj / "_bmad" / "custom"
        _write(cdir / "bmad-prd.toml", "prd-v1\n")
        _write(cdir / "config.toml", f"base-{proj}\n")  # must be excluded
        _write(cdir / "config.user.toml", f"user-{proj}\n")  # must be excluded
    # canonical-only overlay (the auto-dev analogue)
    _write(root / "odyssey" / "_bmad" / "custom" / "bmad-auto-dev.toml", "guard\n")
    # brainstorming steps: step-01 == upstream (clean), step-02a != upstream (fork)
    up = tmp_path / "upstream" / "bmad-brainstorming" / "steps"
    _write(up / "step-01-session-setup.md", "clean\n")
    _write(up / "step-02a-user-selected.md", "UPSTREAM 7 cols\n")
    for proj in ("odyssey", "legal"):
        steps = root / proj / osync.FORK_SKILL_STEPS
        _write(steps / "step-01-session-setup.md", "clean\n")
        _write(steps / "step-02a-user-selected.md", "FORKED 3 cols\n")
    return root


# --- pure utilities --------------------------------------------------------


def test_md5_and_atomic_copy(tmp_path: Path) -> None:
    src = _write(tmp_path / "a.txt", "hello\n")
    dst = tmp_path / "sub" / "b.txt"
    osync.atomic_copy(src, dst)
    assert dst.read_text(encoding="utf-8") == "hello\n"
    assert osync.md5(src) == osync.md5(dst)


def test_discover_overlays_excludes_config(tree: Path) -> None:
    overlays = osync.discover_overlays(tree / "odyssey")
    assert "bmad-prd.toml" in overlays
    assert "bmad-auto-dev.toml" in overlays
    assert "config.toml" not in overlays
    assert "config.user.toml" not in overlays


def test_load_exempt(tmp_path: Path) -> None:
    p = _write(
        tmp_path / "exempt.yaml",
        "- artifact: bmad-create-story.toml\n"
        "  project: Antares\n"
        "  reason: by-design fork\n"
        "  expires: 2026-12-01\n",
    )
    ex = osync.load_exempt(p)
    assert len(ex) == 1
    assert ex[0].artifact == "bmad-create-story.toml"
    assert ex[0].project == "Antares"
    assert osync.is_exempt(ex, "bmad-create-story.toml", "Antares")
    assert osync.is_exempt(ex, "bmad-create-story.toml", "legal") is None
    assert osync.load_exempt(tmp_path / "nope.yaml") == []


# --- census (Этап 1.5) -----------------------------------------------------


def test_fork_census_classifies(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # step-01 worktree==upstream (clean), step-02a worktree!=upstream (fork).
    # head == upstream for both => step-02a is an uncommitted-fork.
    steps_up = tree.parent / "upstream" / "bmad-brainstorming" / "steps"

    def _upstream_bytes(rel: str) -> bytes | None:
        up = steps_up / Path(rel).name
        return up.read_bytes() if up.exists() else None

    monkeypatch.setattr(osync, "git_show_head", lambda repo, rel: _upstream_bytes(rel))
    census = osync.fork_census(tree / "odyssey", steps_up)
    by_name = {Path(e.rel).name: e for e in census}
    assert by_name["step-02a-user-selected.md"].is_fork is True
    assert by_name["step-02a-user-selected.md"].head_relation == "uncommitted-fork"
    assert by_name["step-01-session-setup.md"].is_fork is False
    assert by_name["step-01-session-setup.md"].head_relation == "clean"
    assert sum(1 for e in census if e.is_fork) == 1


def test_census_vendor_bump_not_fork(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # worktree == upstream but HEAD is an OLD different version => vendor-bump, NOT a fork.
    monkeypatch.setattr(osync, "git_show_head", lambda repo, rel: b"OLD 6.7.1 content\n")
    steps_up = tree.parent / "upstream" / "bmad-brainstorming" / "steps"
    census = osync.fork_census(tree / "odyssey", steps_up)
    by_name = {Path(e.rel).name: e for e in census}
    e = by_name["step-01-session-setup.md"]  # worktree==upstream
    assert e.is_fork is False
    assert e.head_relation == "vendor-bump"


# --- invariants + plan -----------------------------------------------------


def _manifest(tree: Path, monkeypatch: pytest.MonkeyPatch, exemptions=None):
    monkeypatch.setattr(osync, "git_show_head", lambda repo, rel: None)
    steps_up = tree.parent / "upstream" / "bmad-brainstorming" / "steps"
    return osync.build_manifest(tree, "odyssey", ["odyssey", "legal"], steps_up, exemptions or [])


def test_invariant_present_and_identical(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    m = _manifest(tree, monkeypatch)
    # silence INV-PERSIST (no git in tmp): only look at PRESENT/IDENTICAL
    findings = [f for f in osync.run_invariants(m, tree, []) if f.inv != "INV-PERSIST"]
    invs = {(f.inv, f.artifact, f.project) for f in findings}
    # auto-dev present only in odyssey -> missing in legal
    assert ("INV-OVERLAY-PRESENT", "bmad-auto-dev.toml", "legal") in invs
    # step-02a fork differs? no — both projects have identical "FORKED 3 cols" -> no finding
    # prd identical -> no finding
    assert not any(f.artifact == "bmad-prd.toml" for f in findings)


def test_invariant_stale_then_exempt(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # make legal's prd differ
    _write(tree / "legal" / "_bmad" / "custom" / "bmad-prd.toml", "prd-DIFFERENT\n")
    m = _manifest(tree, monkeypatch)
    findings = [f for f in osync.run_invariants(m, tree, []) if f.inv != "INV-PERSIST"]
    stale = [f for f in findings if f.artifact == "bmad-prd.toml" and f.project == "legal"]
    assert stale and stale[0].severity == "error"
    # now exempt it -> downgraded to warn
    ex = [osync.Exemption("bmad-prd.toml", "legal", "intentional")]
    m2 = _manifest(tree, monkeypatch, ex)
    findings2 = [f for f in osync.run_invariants(m2, tree, ex) if f.inv != "INV-PERSIST"]
    stale2 = [f for f in findings2 if f.artifact == "bmad-prd.toml" and f.project == "legal"]
    assert stale2 and stale2[0].severity == "warn"


def test_build_plan_create_and_replace(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tree / "legal" / "_bmad" / "custom" / "bmad-prd.toml", "prd-DIFFERENT\n")
    m = _manifest(tree, monkeypatch)
    plan = osync.build_plan(m, tree, [])
    actions = {(it.action, it.artifact, it.project) for it in plan}
    assert ("CREATE", "bmad-auto-dev.toml", "legal") in actions
    assert ("REPLACE", "bmad-prd.toml", "legal") in actions


def test_plan_respects_exempt(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tree / "legal" / "_bmad" / "custom" / "bmad-prd.toml", "prd-DIFFERENT\n")
    ex = [osync.Exemption("bmad-prd.toml", "legal", "intentional")]
    m = _manifest(tree, monkeypatch, ex)
    plan = osync.build_plan(m, tree, ex)
    assert not any(it.artifact == "bmad-prd.toml" for it in plan)


# --- safety ----------------------------------------------------------------


def test_git_guard_fail_closed_on_non_repo(tmp_path: Path) -> None:
    g = osync.git_guard(tmp_path, ["whatever"])
    assert g.ok is False  # not a git work tree => refuse


def test_apply_then_rollback(tmp_path: Path) -> None:
    root = tmp_path / "root"
    src = _write(root / "odyssey" / "f.toml", "NEW\n")
    dst = _write(root / "legal" / "f.toml", "OLD\n")
    item = osync.PlanItem("REPLACE", "f.toml", "legal", src, dst)
    # bypass git_guard for this pure apply/rollback test
    import unittest.mock as mock

    with mock.patch.object(osync, "git_guard", return_value=osync.GitGuard(True, "ok")):
        backup = root / "backups"
        rb = osync.apply_plan([item], root, backup)
        assert dst.read_text(encoding="utf-8") == "NEW\n"
        osync.rollback_plan(rb, root)
        assert dst.read_text(encoding="utf-8") == "OLD\n"
