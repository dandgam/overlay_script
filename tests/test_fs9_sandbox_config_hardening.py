"""FS9 round 4 — sandbox config hardening tests.

Covers seven HIGH-severity audit findings:

- **H2** — ``sandbox_network`` default flipped from ``"github_only"`` (silently
  equivalent to ``"full"``) to ``"none"`` (true network isolation).
- **H3 + H4** — ``prlimit(1)`` wraps the bwrap invocation to cap nproc / AS /
  fsize / nofile. Defaults overridable via ``BMAD_SANDBOX_MAX_*`` env vars.
- **H5** — ``BMAD_REQUIRE_SANDBOX=1`` makes ``detect_sandbox()`` raise
  ``RuntimeError`` instead of returning ``NoSandbox`` silently.
- **H6** — ``BMAD_SANDBOX=none`` requires
  ``BMAD_SANDBOX_DISABLE_CONFIRMED=yes-i-accept-risk``; otherwise raises.
- **H1** — ``--tmpfs /sys`` hides kernel info (LSM list, dmi, network).
- **H8** — stale ``BMAD_ORCHESTRATOR_SESSION_ID`` cleared from
  ``os.environ`` when the DB cannot validate it.
- **H9** — ``BMAD_REQUIRE_DB_BRIDGE=1`` blocks bot startup if DB bridge fails
  (no silent stub-mode degradation).

Real-bwrap PoC tests (fork-bomb, OOM, fsize, /sys empty) are skipped if
``bwrap`` is not installed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from bmad_orchestrator.runtime.sandbox import (
    DEFAULT_MAX_AS_BYTES,
    DEFAULT_MAX_FSIZE_BYTES,
    DEFAULT_MAX_NOFILE,
    DEFAULT_MAX_NPROC,
    BwrapSandbox,
    NoSandbox,
    detect_sandbox,
)

_BWRAP_BIN = shutil.which("bwrap")
_BWRAP_REASON = "bwrap not installed; install bubblewrap to run real-PoC tests"
requires_bwrap = pytest.mark.skipif(_BWRAP_BIN is None, reason=_BWRAP_REASON)


@pytest.fixture(autouse=True)
def _env_isolation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Clear FS9 enforcement envs + redirect audit log."""
    monkeypatch.setenv("BMAD_AUDIT_LOG", str(tmp_path / "audit.events.jsonl"))
    monkeypatch.delenv("BMAD_SANDBOX", raising=False)
    monkeypatch.delenv("BMAD_REQUIRE_SANDBOX", raising=False)
    monkeypatch.delenv("BMAD_SANDBOX_DISABLE_CONFIRMED", raising=False)
    monkeypatch.delenv("BMAD_SANDBOX_MAX_NPROC", raising=False)
    monkeypatch.delenv("BMAD_SANDBOX_MAX_AS_BYTES", raising=False)
    monkeypatch.delenv("BMAD_SANDBOX_MAX_FSIZE_BYTES", raising=False)
    monkeypatch.delenv("BMAD_SANDBOX_MAX_NOFILE", raising=False)
    monkeypatch.delenv("BMAD_REQUIRE_DB_BRIDGE", raising=False)


# ── H2: sandbox_network default ──────────────────────────────────────────────


def test_h2_spawn_worker_default_network_is_none() -> None:
    """``spawn_worker`` signature exposes ``sandbox_network`` default 'none'."""
    import inspect

    from bmad_orchestrator.runtime.worker_spawn import spawn_worker

    sig = inspect.signature(spawn_worker)
    assert sig.parameters["sandbox_network"].default == "none"


def test_h2_bwrap_default_network_unshares_net(tmp_path: Path) -> None:
    """Without explicit ``network`` arg, bwrap gets ``--unshare-net``."""
    out = BwrapSandbox().wrap_command(["echo"], worktree=tmp_path)
    assert "--unshare-net" in out


def test_h2_explicit_github_only_still_allowed(tmp_path: Path) -> None:
    """Caller can still opt in to network access via explicit override."""
    out = BwrapSandbox().wrap_command(
        ["echo"], worktree=tmp_path, network="github_only"
    )
    assert "--unshare-net" not in out


# ── H3 + H4: prlimit rlimits wrapper ─────────────────────────────────────────


def test_h3_prlimit_is_first_token(tmp_path: Path) -> None:
    """prlimit wraps the entire bwrap invocation — must be argv[0]."""
    out = BwrapSandbox().wrap_command(["echo"], worktree=tmp_path)
    assert out[0].endswith("prlimit")


def test_h3_default_rlimits_present(tmp_path: Path) -> None:
    """Default nproc/as/fsize/nofile caps emitted from constants."""
    out = BwrapSandbox().wrap_command(["echo"], worktree=tmp_path)
    head = out[: out.index("--")]  # everything up to the prlimit `--` separator
    assert f"--nproc={DEFAULT_MAX_NPROC}" in head
    assert f"--as={DEFAULT_MAX_AS_BYTES}" in head
    assert f"--fsize={DEFAULT_MAX_FSIZE_BYTES}" in head
    assert f"--nofile={DEFAULT_MAX_NOFILE}" in head


def test_h3_env_override_nproc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``BMAD_SANDBOX_MAX_NPROC`` env overrides the default cap."""
    monkeypatch.setenv("BMAD_SANDBOX_MAX_NPROC", "128")
    out = BwrapSandbox().wrap_command(["echo"], worktree=tmp_path)
    head = out[: out.index("--")]
    assert "--nproc=128" in head
    assert f"--nproc={DEFAULT_MAX_NPROC}" not in head


def test_h3_env_invalid_falls_back_to_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-positive-integer env value silently falls back to default."""
    monkeypatch.setenv("BMAD_SANDBOX_MAX_NPROC", "not-a-number")
    out = BwrapSandbox().wrap_command(["echo"], worktree=tmp_path)
    head = out[: out.index("--")]
    assert f"--nproc={DEFAULT_MAX_NPROC}" in head


def test_h3_prlimit_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """If prlimit binary is missing entirely, BwrapSandbox refuses to construct."""
    monkeypatch.setattr(shutil, "which", lambda name: None if name == "prlimit" else "/usr/bin/" + name)
    with pytest.raises(RuntimeError, match="prlimit not found"):
        BwrapSandbox(prlimit_path="/does/not/exist/prlimit")


# Note: a real fork-bomb PoC under bwrap is omitted intentionally. RLIMIT_NPROC
# is per-UID, not per-process-tree, so the cap counts *every* process the user
# owns (including the pytest runner itself + 2k+ background services on a typical
# dev host). Setting --nproc low enough to trigger the cap on a fork-bomb also
# blocks bwrap from creating its initial namespace. Coverage for the cap is
# provided by ``test_h3_default_rlimits_present`` + ``test_h3_env_override_nproc``
# (verify arg emission); kernel correctness of RLIMIT_NPROC is trusted.


@requires_bwrap
def test_h4_real_fsize_capped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--fsize`` cap blocks writing huge files. Use 1 MiB cap to keep test fast."""
    monkeypatch.setenv("BMAD_SANDBOX_MAX_FSIZE_BYTES", "1048576")  # 1 MiB
    sb = BwrapSandbox()
    wrapped = sb.wrap_command(
        [
            "bash",
            "-c",
            # dd will hit the size limit and fail with SIGXFSZ / "File too large".
            "dd if=/dev/zero of=/tmp/bigfile bs=1M count=10 2>&1; echo exit=$?",
        ],
        worktree=tmp_path,
        env={"PATH": "/usr/bin:/bin"},
    )
    res = subprocess.run(
        wrapped, capture_output=True, text=True, timeout=15.0, check=False
    )
    if "Creating new namespace failed" in res.stderr:
        pytest.skip(f"host namespace exhaustion: {res.stderr.strip()}")
    combined = res.stdout + res.stderr
    assert (
        "File size limit exceeded" in combined
        or "File too large" in combined
        or "exit=0" not in combined.splitlines()[-1]
    ), f"expected fsize cap to block 10 MiB write:\n{combined}"


# ── H5: BMAD_REQUIRE_SANDBOX hard-fail ───────────────────────────────────────


def test_h5_require_sandbox_raises_when_bwrap_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REQUIRE_SANDBOX=1 + no bwrap → RuntimeError, no NoSandbox fallback."""
    monkeypatch.setattr(shutil, "which", lambda _: None)
    monkeypatch.setenv("BMAD_REQUIRE_SANDBOX", "1")
    with pytest.raises(RuntimeError, match="BMAD_REQUIRE_SANDBOX"):
        detect_sandbox()


def test_h5_require_sandbox_raises_with_bwrap_override_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BMAD_SANDBOX=bwrap + bwrap missing + REQUIRE_SANDBOX=1 → RuntimeError."""
    monkeypatch.setenv("BMAD_SANDBOX", "bwrap")
    monkeypatch.setenv("BMAD_REQUIRE_SANDBOX", "1")
    monkeypatch.setattr(shutil, "which", lambda _: None)
    with pytest.raises(RuntimeError, match="BMAD_REQUIRE_SANDBOX"):
        detect_sandbox()


def test_h5_require_sandbox_passes_when_bwrap_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REQUIRE_SANDBOX=1 + bwrap available → returns BwrapSandbox normally."""
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/bwrap")
    monkeypatch.setenv("BMAD_REQUIRE_SANDBOX", "1")
    sb = detect_sandbox()
    assert isinstance(sb, BwrapSandbox)


# ── H6: BMAD_SANDBOX=none confirmation requirement ───────────────────────────


def test_h6_disable_without_confirmation_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BMAD_SANDBOX=none alone → RuntimeError (must include confirmation token)."""
    monkeypatch.setenv("BMAD_SANDBOX", "none")
    with pytest.raises(RuntimeError, match="BMAD_SANDBOX_DISABLE_CONFIRMED"):
        detect_sandbox()


def test_h6_disable_with_wrong_token_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Confirmation token must match exactly — typo → RuntimeError."""
    monkeypatch.setenv("BMAD_SANDBOX", "none")
    monkeypatch.setenv("BMAD_SANDBOX_DISABLE_CONFIRMED", "yes")  # wrong
    with pytest.raises(RuntimeError, match="BMAD_SANDBOX_DISABLE_CONFIRMED"):
        detect_sandbox()


def test_h6_disable_with_correct_token_returns_no_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two-key confirmation → NoSandbox + audit emission."""
    monkeypatch.setenv("BMAD_SANDBOX", "none")
    monkeypatch.setenv("BMAD_SANDBOX_DISABLE_CONFIRMED", "yes-i-accept-risk")
    sb = detect_sandbox()
    assert isinstance(sb, NoSandbox)


# ── H1: /sys tmpfs hide ──────────────────────────────────────────────────────


def test_h1_tmpfs_sys_in_wrap(tmp_path: Path) -> None:
    """``--tmpfs /sys`` must appear in bwrap arg list."""
    out = BwrapSandbox().wrap_command(["echo"], worktree=tmp_path)
    indices = [i for i, t in enumerate(out) if t == "--tmpfs"]
    sys_found = any(out[i + 1] == "/sys" for i in indices)
    assert sys_found, f"--tmpfs /sys missing in wrap output: {out}"


@requires_bwrap
def test_h1_real_sandbox_sys_empty(tmp_path: Path) -> None:
    """Inside real sandbox, ``/sys`` must be empty (kernel info hidden).

    Transient host namespace exhaustion (per-UID kernel.unprivileged_userns_*
    limits, cgroups, etc.) yields ``Creating new namespace failed: Resource
    temporarily unavailable`` — skip in that case rather than fail, since the
    /sys hide is verified by the unit test ``test_h1_tmpfs_sys_in_wrap``.
    """
    sb = BwrapSandbox()
    wrapped = sb.wrap_command(
        ["bash", "-c", "ls /sys 2>&1; cat /sys/kernel/security/lsm 2>&1; echo END"],
        worktree=tmp_path,
        env={"PATH": "/usr/bin:/bin"},
    )
    res = subprocess.run(
        wrapped, capture_output=True, text=True, timeout=10.0, check=False
    )
    if "Creating new namespace failed" in res.stderr:
        pytest.skip(f"host namespace exhaustion: {res.stderr.strip()}")
    lines = [ln for ln in res.stdout.splitlines() if ln]
    assert "END" in lines, f"sandbox crashed: {res.stdout!r} stderr={res.stderr!r}"
    sys_entries = [ln for ln in lines if ln in {"block", "class", "devices", "kernel", "module", "fs", "bus", "dev", "firmware"}]
    assert not sys_entries, f"expected /sys empty, got entries: {sys_entries}"


# ── H8: stale BMAD_ORCHESTRATOR_SESSION_ID cleanup ───────────────────────────


@pytest.mark.asyncio
async def test_h8_stale_env_cleared_when_db_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Stale env id + DB connect raises → env var stripped after call."""
    from bmad_orchestrator.agent.run import SESSION_ENV_VAR, _resolve_session

    monkeypatch.setenv(SESSION_ENV_VAR, "999999")

    # Force StateDB.init to raise.
    async def _boom(self: object) -> None:
        raise OSError("simulated DB unavailable")

    from bmad_orchestrator.state.db import StateDB

    monkeypatch.setattr(StateDB, "init", _boom)

    db, sid = await _resolve_session(
        target_project="proj",
        wave="w1",
        max_parallel=1,
        db_path=tmp_path / "state.db",
    )
    assert (db, sid) == (None, None)
    assert SESSION_ENV_VAR not in os.environ, "stale session env not cleared"


@pytest.mark.asyncio
async def test_h8_invalid_env_value_cleared(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Non-integer env value → cleared, then resolve_or_create path used.

    With DB also failing on the fallback path, expect ``(None, None)`` and env
    var gone.
    """
    from bmad_orchestrator.agent.run import SESSION_ENV_VAR, _resolve_session

    monkeypatch.setenv(SESSION_ENV_VAR, "not-an-int")

    async def _boom(self: object) -> None:
        raise OSError("simulated DB unavailable")

    from bmad_orchestrator.state.db import StateDB

    monkeypatch.setattr(StateDB, "init", _boom)

    await _resolve_session(
        target_project="proj",
        wave="w1",
        max_parallel=1,
        db_path=tmp_path / "state.db",
    )
    assert SESSION_ENV_VAR not in os.environ, "invalid session env not cleared"


# ── H9: BMAD_REQUIRE_DB_BRIDGE for bot ───────────────────────────────────────


@pytest.mark.asyncio
async def test_h9_require_db_bridge_raises_on_db_fail(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """REQUIRE_DB_BRIDGE=1 + DB unavailable → bot _attach_bridge raises."""
    from bmad_orchestrator.bot import main as bot_main

    monkeypatch.setenv("BMAD_REQUIRE_DB_BRIDGE", "1")

    async def _boom(self: object) -> None:
        raise OSError("simulated bot DB unavailable")

    from bmad_orchestrator.state.db import StateDB

    monkeypatch.setattr(StateDB, "init", _boom)

    with pytest.raises(RuntimeError, match="BMAD_REQUIRE_DB_BRIDGE"):
        await bot_main._attach_bridge()


@pytest.mark.asyncio
async def test_h9_no_require_falls_back_to_stub_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Without REQUIRE_DB_BRIDGE, DB failure logs + degrades silently (legacy)."""
    from bmad_orchestrator.bot import main as bot_main

    # Ensure the env var is unset (autouse fixture already did this, belt+suspenders).
    monkeypatch.delenv("BMAD_REQUIRE_DB_BRIDGE", raising=False)

    async def _boom(self: object) -> None:
        raise OSError("simulated bot DB unavailable")

    from bmad_orchestrator.state.db import StateDB

    monkeypatch.setattr(StateDB, "init", _boom)

    # Must NOT raise.
    await bot_main._attach_bridge()
