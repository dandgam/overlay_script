"""Initiative #1 Task 1.3 + 1.4 — cgroup limits + per-worker HOME isolation.

Three groups of tests:

* sandbox.wrap_command — verifies the cgroup prefix and HOME-overlay binds
  are spliced into the bwrap argv in the right places, and that opt-out
  paths (no cgroup_limits, no worker_home_overlay) preserve legacy behaviour.

* _create_isolated_home / _cleanup_isolated_home — smoke + safety tests for
  the snapshot helper (.claude tree minus the heavy ``projects/`` subdir).

* spawn_worker — mock-mode assertions that the new parameters don't break
  the existing event JSONL contract.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from bmad_orchestrator.runtime.sandbox import (
    DEFAULT_CGROUP_CPU_QUOTA,
    DEFAULT_CGROUP_LIMITS,
    DEFAULT_CGROUP_MEMORY_MAX,
    DEFAULT_CGROUP_TASKS_MAX,
    BwrapSandbox,
    NoSandbox,
    _build_cgroup_prefix,
    _systemd_run_available,
)
from bmad_orchestrator.runtime.worker_spawn import (
    ALLOWED_WORKER_ENV,
    _build_worker_env,
    _cleanup_isolated_home,
    _create_isolated_home,
    spawn_worker,
)


@pytest.fixture(autouse=True)
def _audit_log_isolation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Redirect audit + clear cgroup enforcement env so tests run on any host."""
    monkeypatch.setenv("BMAD_AUDIT_LOG", str(tmp_path / "audit.events.jsonl"))
    monkeypatch.delenv("BMAD_REQUIRE_SANDBOX", raising=False)
    monkeypatch.delenv("BMAD_REQUIRE_CGROUP", raising=False)
    monkeypatch.delenv("BMAD_SANDBOX_DISABLE_CONFIRMED", raising=False)


# ── DEFAULT_CGROUP_LIMITS shape ───────────────────────────────────────────────


def test_default_cgroup_limits_shape() -> None:
    assert DEFAULT_CGROUP_LIMITS == {
        "MemoryMax": DEFAULT_CGROUP_MEMORY_MAX,
        "CPUQuota": DEFAULT_CGROUP_CPU_QUOTA,
        "TasksMax": DEFAULT_CGROUP_TASKS_MAX,
    }
    assert DEFAULT_CGROUP_MEMORY_MAX == "8G"
    assert DEFAULT_CGROUP_CPU_QUOTA == "200%"
    assert DEFAULT_CGROUP_TASKS_MAX == "16384"


# ── cgroup prefix builder ─────────────────────────────────────────────────────


def test_build_cgroup_prefix_skips_when_systemd_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No systemd-run on PATH → returns None, no exception (degraded mode)."""
    monkeypatch.setattr(
        "bmad_orchestrator.runtime.sandbox._systemd_run_available",
        lambda: (False, None),
    )
    out = _build_cgroup_prefix({"MemoryMax": "1G"}, worktree=tmp_path)
    assert out is None


def test_build_cgroup_prefix_hard_fails_when_required(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """BMAD_REQUIRE_CGROUP=1 + no systemd-run → RuntimeError."""
    monkeypatch.setattr(
        "bmad_orchestrator.runtime.sandbox._systemd_run_available",
        lambda: (False, None),
    )
    monkeypatch.setenv("BMAD_REQUIRE_CGROUP", "1")
    with pytest.raises(RuntimeError, match="BMAD_REQUIRE_CGROUP=1"):
        _build_cgroup_prefix({"MemoryMax": "1G"}, worktree=tmp_path)


def test_build_cgroup_prefix_when_available(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """systemd-run available → prefix has --user --scope -p KEY=VALUE -- terminator."""
    fake_path = "/usr/bin/systemd-run"
    monkeypatch.setattr(
        "bmad_orchestrator.runtime.sandbox._systemd_run_available",
        lambda: (True, fake_path),
    )
    out = _build_cgroup_prefix(
        {"MemoryMax": "8G", "CPUQuota": "200%"}, worktree=tmp_path
    )
    assert out is not None
    assert out[0] == fake_path
    assert "--user" in out
    assert "--scope" in out
    assert out[-1] == "--"
    # Ensure every limit is emitted with the -p KEY=VALUE pair.
    assert "-p" in out
    joined = " ".join(out)
    assert "MemoryMax=8G" in joined
    assert "CPUQuota=200%" in joined
    # Scope name pinned per spawn so we can systemctl --user cancel it.
    unit_args = [a for a in out if a.startswith("--unit=")]
    assert len(unit_args) == 1
    assert unit_args[0].endswith(".scope")


def test_systemd_run_available_requires_xdg_runtime(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """systemd-run on PATH but no XDG_RUNTIME_DIR → not available."""
    monkeypatch.setattr(
        "bmad_orchestrator.runtime.sandbox.shutil.which",
        lambda name: "/usr/bin/systemd-run" if name == "systemd-run" else None,
    )
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    available, _ = _systemd_run_available()
    assert available is False


# ── BwrapSandbox cgroup splicing ──────────────────────────────────────────────


def test_bwrap_wrap_command_no_cgroup_when_omitted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Default call path (no cgroup_limits kwarg) keeps the legacy prefix order:
    prlimit comes first, no systemd-run anywhere."""
    monkeypatch.setattr(
        "bmad_orchestrator.runtime.sandbox._systemd_run_available",
        lambda: (True, "/usr/bin/systemd-run"),  # available, but unused
    )
    sb = BwrapSandbox(bwrap_path="/usr/bin/bwrap", prlimit_path="/usr/bin/prlimit")
    out = sb.wrap_command(["echo", "x"], worktree=tmp_path)
    assert out[0] == "/usr/bin/prlimit"
    assert "/usr/bin/systemd-run" not in out


def test_bwrap_wrap_command_cgroup_is_outermost(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """systemd-run --scope must wrap prlimit + bwrap (outer-to-inner ordering)."""
    monkeypatch.setattr(
        "bmad_orchestrator.runtime.sandbox._systemd_run_available",
        lambda: (True, "/usr/bin/systemd-run"),
    )
    sb = BwrapSandbox(bwrap_path="/usr/bin/bwrap", prlimit_path="/usr/bin/prlimit")
    out = sb.wrap_command(
        ["echo", "x"], worktree=tmp_path, cgroup_limits={"MemoryMax": "1G"}
    )
    sd_idx = out.index("/usr/bin/systemd-run")
    pl_idx = out.index("/usr/bin/prlimit")
    bw_idx = out.index("/usr/bin/bwrap")
    assert sd_idx < pl_idx < bw_idx, f"order broken: {out[:10]}"


# ── D-Bus / systemd-run env regression (2026-05-19) ───────────────────────────
#
# Bug: cgroup-wrapped workers died instantly with "Failed to connect to bus:
# No medium found". Root cause — ``systemd-run --user`` (outer cgroup wrapper)
# ran with a worker env that lacked XDG_RUNTIME_DIR + DBUS_SESSION_BUS_ADDRESS,
# so it could not reach the user systemd manager. Fix: forward both vars in
# ALLOWED_WORKER_ENV (so systemd-run works) but exclude them from the bwrap
# ``--setenv`` list (so the inner sandboxed worker stays isolated).


def test_worker_env_forwards_dbus_vars_for_systemd_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_build_worker_env`` must forward XDG_RUNTIME_DIR + DBUS_SESSION_BUS_ADDRESS
    so the outer ``systemd-run --user`` cgroup wrapper can reach the user bus."""
    assert "XDG_RUNTIME_DIR" in ALLOWED_WORKER_ENV
    assert "DBUS_SESSION_BUS_ADDRESS" in ALLOWED_WORKER_ENV
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus")
    env = _build_worker_env(None)
    assert env["XDG_RUNTIME_DIR"] == "/run/user/1000"
    assert env["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/run/user/1000/bus"


def test_bwrap_excludes_dbus_vars_from_inner_setenv(tmp_path: Path) -> None:
    """The inner sandboxed worker must NOT receive the session-bus env — those
    vars are for the outer systemd-run wrapper only. ``wrap_command`` excludes
    them from its ``--setenv`` list (D-Bus isolation preserved)."""
    sb = BwrapSandbox(bwrap_path="/usr/bin/bwrap", prlimit_path="/usr/bin/prlimit")
    out = sb.wrap_command(
        ["echo", "x"],
        worktree=tmp_path,
        env={
            "PATH": "/usr/bin",
            "XDG_RUNTIME_DIR": "/run/user/1000",
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
        },
    )
    # PATH is forwarded via --setenv; the two bus vars are NOT.
    assert "PATH" in out
    assert "XDG_RUNTIME_DIR" not in out
    assert "DBUS_SESSION_BUS_ADDRESS" not in out


def test_bwrap_wrap_command_cgroup_skipped_when_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """cgroup_limits requested but systemd-run absent → quietly skip (degraded)."""
    monkeypatch.setattr(
        "bmad_orchestrator.runtime.sandbox._systemd_run_available",
        lambda: (False, None),
    )
    sb = BwrapSandbox(bwrap_path="/usr/bin/bwrap", prlimit_path="/usr/bin/prlimit")
    out = sb.wrap_command(
        ["echo", "x"], worktree=tmp_path, cgroup_limits={"MemoryMax": "1G"}
    )
    assert out[0] == "/usr/bin/prlimit"


# ── BwrapSandbox HOME overlay binds ───────────────────────────────────────────


def test_bwrap_wrap_command_uses_overlay_paths(tmp_path: Path) -> None:
    """worker_home_overlay → bwrap binds overlay/.claude over host ~/.claude."""
    overlay = tmp_path / "overlay"
    (overlay / ".claude").mkdir(parents=True)
    (overlay / ".claude" / "settings.json").write_text("{}")
    (overlay / ".claude.json").write_text('{"v":1}')
    (overlay / ".local" / "share" / "claude").mkdir(parents=True)

    sb = BwrapSandbox(bwrap_path="/usr/bin/bwrap", prlimit_path="/usr/bin/prlimit")
    out = sb.wrap_command(
        ["echo", "x"], worktree=tmp_path, worker_home_overlay=overlay
    )

    # Find every --bind src dest triple and confirm the overlay copy maps to
    # the original host path (so $HOME-resolution inside bwrap still works).
    home = os.path.expanduser("~")
    binds = []
    for i, tok in enumerate(out):
        if tok == "--bind" and i + 2 < len(out):
            binds.append((out[i + 1], out[i + 2]))
    sources = {src for src, _ in binds}
    targets = {dest for _, dest in binds}

    assert str(overlay / ".claude") in sources
    assert str(overlay / ".claude.json") in sources
    assert f"{home}/.claude" in targets
    assert f"{home}/.claude.json" in targets


def test_bwrap_wrap_command_rejects_missing_overlay(tmp_path: Path) -> None:
    sb = BwrapSandbox(bwrap_path="/usr/bin/bwrap", prlimit_path="/usr/bin/prlimit")
    with pytest.raises(FileNotFoundError, match="worker_home_overlay does not exist"):
        sb.wrap_command(
            ["echo", "x"],
            worktree=tmp_path,
            worker_home_overlay=tmp_path / "missing",
        )


def test_bwrap_wrap_command_rejects_relative_overlay(tmp_path: Path) -> None:
    sb = BwrapSandbox(bwrap_path="/usr/bin/bwrap", prlimit_path="/usr/bin/prlimit")
    with pytest.raises(ValueError, match="must be absolute"):
        sb.wrap_command(
            ["echo", "x"],
            worktree=tmp_path,
            worker_home_overlay=Path("relative/path"),
        )


# ── NoSandbox parity ──────────────────────────────────────────────────────────


def test_no_sandbox_accepts_new_kwargs(tmp_path: Path) -> None:
    """NoSandbox is a true pass-through and must accept (and ignore) the new
    cgroup/overlay kwargs without breaking the Protocol contract."""
    out = NoSandbox().wrap_command(
        ["echo", "x"],
        worktree=tmp_path,
        worker_home_overlay=tmp_path,
        cgroup_limits={"MemoryMax": "1G"},
    )
    assert out == ["echo", "x"]


# ── _create_isolated_home helper ──────────────────────────────────────────────


def test_create_isolated_home_copies_settings_skips_large_subdirs(
    tmp_path: Path,
) -> None:
    fake_home = tmp_path / "fake-home"
    (fake_home / ".claude").mkdir(parents=True)
    (fake_home / ".claude" / "settings.json").write_text('{"v":1}')
    # ``projects/`` is the heavy session history — we want it skipped.
    (fake_home / ".claude" / "projects").mkdir()
    (fake_home / ".claude" / "projects" / "huge.json").write_text("x" * 1024)
    (fake_home / ".claude.json").write_text("{}")

    overlay = _create_isolated_home(worker_label="ut", host_home=fake_home)
    try:
        assert (overlay / ".claude" / "settings.json").read_text() == '{"v":1}'
        assert (overlay / ".claude.json").read_text() == "{}"
        # projects/ exists as placeholder but is empty (heavy data not copied).
        assert (overlay / ".claude" / "projects").is_dir()
        assert list((overlay / ".claude" / "projects").iterdir()) == []
        # .local/share/claude target dir created for bwrap bind.
        assert (overlay / ".local" / "share" / "claude").is_dir()
    finally:
        _cleanup_isolated_home(overlay)


def test_create_isolated_home_when_host_missing(tmp_path: Path) -> None:
    """Missing host .claude — overlay still created with empty placeholders."""
    overlay = _create_isolated_home(worker_label="empty", host_home=tmp_path / "x")
    try:
        # No .claude or .claude.json copied (host had none) — overlay just
        # has the .local/share/claude placeholder for bwrap bind.
        assert (overlay / ".local" / "share" / "claude").is_dir()
        assert not (overlay / ".claude.json").exists()
    finally:
        _cleanup_isolated_home(overlay)


def test_cleanup_isolated_home_refuses_outside_tmp(tmp_path: Path) -> None:
    """Safety: only paths under tempfile.gettempdir() get rm-rf'd. A bug that
    passed a project path here must NOT nuke it."""
    project_like = tmp_path / "not-tmp"
    project_like.mkdir()
    (project_like / "important.txt").write_text("keep me")
    _cleanup_isolated_home(project_like)  # must be no-op
    assert (project_like / "important.txt").read_text() == "keep me"


def test_cleanup_isolated_home_none_is_noop() -> None:
    _cleanup_isolated_home(None)  # must not raise


def test_cleanup_isolated_home_missing_is_noop(tmp_path: Path) -> None:
    """Path under tempdir but non-existent → no-op, no exception."""
    fake = Path(tempfile.gettempdir()) / "bmad-worker-doesnotexist-12345"
    assert not fake.exists()
    _cleanup_isolated_home(fake)  # must not raise


# ── spawn_worker mock-mode contract ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_spawn_worker_mock_accepts_new_kwargs(tmp_path: Path) -> None:
    """Mock mode must accept isolated_home + cgroup_limits without spawning
    a real subprocess. The new fields land on the handle for observability."""
    wt = tmp_path / "wt"
    wt.mkdir()
    handle = await spawn_worker(
        worktree=str(wt),
        story_id="x.y.z",
        branch="feature/test",
        mock=True,
        isolated_home=True,
        cgroup_limits=DEFAULT_CGROUP_LIMITS,
    )
    assert handle.mock is True
    # Mock mode does not snapshot HOME (no sandbox involved) — both fields
    # stay at their defaults, which IS the documented contract.
    assert handle.isolated_home_path is None
    assert handle.cgroup_limits_applied is None
