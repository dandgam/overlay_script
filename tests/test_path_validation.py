"""Tests for R3 security minors — CLI path validation helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from bmad_orchestrator.cli.path_validation import (
    DENY_SYSTEM_PREFIXES,
    UnsafePathError,
    ensure_inside_root,
    safe_resolve_path,
)

# ── safe_resolve_path ───────────────────────────────────────────────────────


def test_safe_resolve_accepts_normal_path(tmp_path: Path):
    out = safe_resolve_path(tmp_path / "sub", name="--x")
    assert out == (tmp_path / "sub").resolve()


def test_safe_resolve_expands_user_home(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("HOME", str(tmp_path))
    out = safe_resolve_path(Path("~/work"), name="--x")
    assert out == (tmp_path / "work").resolve()


def test_safe_resolve_rejects_etc():
    with pytest.raises(UnsafePathError) as exc:
        safe_resolve_path(Path("/etc/passwd"), name="--lessons-dir")
    assert "/etc" in str(exc.value)
    assert "--lessons-dir" in str(exc.value)


def test_safe_resolve_rejects_etc_subdir():
    with pytest.raises(UnsafePathError):
        safe_resolve_path(Path("/etc/cron.d/scripts"), name="--x")


def test_safe_resolve_rejects_root_home():
    with pytest.raises(UnsafePathError):
        safe_resolve_path(Path("/root/.ssh"), name="--x")


def test_safe_resolve_rejects_proc():
    with pytest.raises(UnsafePathError):
        safe_resolve_path(Path("/proc/1/cmdline"), name="--x")


def test_safe_resolve_rejects_each_deny_prefix():
    for prefix in DENY_SYSTEM_PREFIXES:
        with pytest.raises(UnsafePathError):
            safe_resolve_path(Path(prefix) / "subpath", name="--x")


def test_safe_resolve_must_exist_passes(tmp_path: Path):
    target = tmp_path / "real"
    target.mkdir()
    out = safe_resolve_path(target, name="--x", must_exist=True)
    assert out == target.resolve()


def test_safe_resolve_must_exist_fails(tmp_path: Path):
    with pytest.raises(UnsafePathError) as exc:
        safe_resolve_path(tmp_path / "missing", name="--lessons-dir", must_exist=True)
    assert "does not exist" in str(exc.value)


def test_safe_resolve_symlink_following_to_safe_target(tmp_path: Path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    out = safe_resolve_path(link, name="--x")
    assert out == real.resolve()


def test_safe_resolve_symlink_to_denied_rejected(tmp_path: Path):
    link = tmp_path / "link-to-etc"
    link.symlink_to("/etc")
    with pytest.raises(UnsafePathError):
        safe_resolve_path(link, name="--x")


def test_safe_resolve_traversal_dot_dot_resolves_safely(tmp_path: Path):
    """`..` is folded out by resolve(); the result is what gets validated."""
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    out = safe_resolve_path(sub / ".." / ".." / "a", name="--x")
    assert out == (tmp_path / "a").resolve()


def test_safe_resolve_traversal_into_denied_rejected(tmp_path: Path):
    """`..` resolving into /etc must be blocked."""
    # Start from a /tmp subdir, traverse up to root, into /etc
    weird = Path("/tmp/whatever") / ".." / ".." / "etc"
    with pytest.raises(UnsafePathError):
        safe_resolve_path(weird, name="--x")


# ── ensure_inside_root ──────────────────────────────────────────────────────


def test_ensure_inside_root_accepts_child(tmp_path: Path):
    root = tmp_path
    child = tmp_path / "lessons" / "p1"
    child.mkdir(parents=True)
    out = ensure_inside_root(
        child, root, child_name="--lessons-dir", root_name="--skills-root"
    )
    assert out == child.resolve()


def test_ensure_inside_root_accepts_equal_path(tmp_path: Path):
    out = ensure_inside_root(
        tmp_path, tmp_path, child_name="--lessons-dir", root_name="--skills-root"
    )
    assert out == tmp_path.resolve()


def test_ensure_inside_root_rejects_sibling(tmp_path: Path):
    root = tmp_path / "root"
    root.mkdir()
    sibling = tmp_path / "elsewhere"
    sibling.mkdir()
    with pytest.raises(UnsafePathError) as exc:
        ensure_inside_root(
            sibling, root, child_name="--lessons-dir", root_name="--skills-root"
        )
    assert "NOT inside" in str(exc.value)


def test_ensure_inside_root_rejects_symlink_escape(tmp_path: Path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link_inside_root = root / "lessons"
    link_inside_root.symlink_to(outside)
    with pytest.raises(UnsafePathError):
        ensure_inside_root(
            link_inside_root,
            root,
            child_name="--lessons-dir",
            root_name="--skills-root",
        )


def test_ensure_inside_root_rejects_dot_dot_escape(tmp_path: Path):
    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "outside").mkdir()
    escape = root / ".." / "outside"
    with pytest.raises(UnsafePathError):
        ensure_inside_root(
            escape, root, child_name="--lessons-dir", root_name="--skills-root"
        )
