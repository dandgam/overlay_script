"""FS10 — 888 phase-chain blackout (Q-260528-BATCH-PIPELINE §O fix#2).

Closes the ops S2 HIGH (worker self-disables the enforce flag via ``config/.env``)
and reinforces S1 CRITICAL at the OS layer (worker cannot forge the supervisor's
verdict-file because the verdict-dir is a private tmpfs).

The fix adds a SECOND blackout pass in ``BwrapSandbox.wrap_command`` AFTER the
``~/.claude`` overlay bind — ordering is load-bearing: the generic blackout loop
runs BEFORE the overlay bind, which would re-expose (shadow) any cache subpath.

* ``~/.claude/skills/888/cache/phase-chain`` → ``--tmpfs`` (writes discarded; host
  copy is the only truth — kills the S1 forge AND the bwrap stall).
* ``~/.claude/skills/888/config/.env`` → ``--ro-bind`` (read-only; worker cannot
  flip ``BMAD_888_PHASE_CHAIN_ENFORCE``).

Argv-inspection tests always run. Real-bwrap PoC tests are skipped when ``bwrap``
is absent (NoSandbox fallback) — documented per the bounce directive.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from bmad_orchestrator.runtime.sandbox import BwrapSandbox

_BWRAP_BIN = shutil.which("bwrap")
requires_bwrap = pytest.mark.skipif(
    _BWRAP_BIN is None,
    reason="bwrap not installed; install bubblewrap to run real-PoC tests",
)

_HOME = Path(os.path.expanduser("~"))
_PHASE_CHAIN = _HOME / ".claude" / "skills" / "888" / "cache" / "phase-chain"
_ENV_FILE = _HOME / ".claude" / "skills" / "888" / "config" / ".env"
_CLAUDE_BIND = _HOME / ".claude"


def _wrap(tmp_path: Path, **kw: object) -> list[str]:
    return BwrapSandbox().wrap_command(["echo"], worktree=tmp_path, **kw)  # type: ignore[arg-type]


# ── Argv inspection (no bwrap execution) ─────────────────────────────────────


def test_phase_chain_tmpfs_present(tmp_path: Path) -> None:
    """verdict-dir/state dir is mounted as a private tmpfs (S1 OS-layer)."""
    out = _wrap(tmp_path)
    assert "--tmpfs" in out
    # the tmpfs destination is the phase-chain dir
    idxs = [i for i, t in enumerate(out) if t == "--tmpfs"]
    dests = {out[i + 1] for i in idxs}
    assert str(_PHASE_CHAIN) in dests, dests


def test_phase_chain_tmpfs_is_post_overlay(tmp_path: Path) -> None:
    """ORDERING: the phase-chain tmpfs must come AFTER the ~/.claude overlay bind,
    else the overlay re-exposes the cache subpath and shadows the blackout."""
    out = _wrap(tmp_path)
    # last index of a --bind whose dest is ~/.claude (the overlay/host bind)
    claude_bind_idx = max(
        (
            i
            for i, t in enumerate(out)
            if t == "--bind" and out[i + 1] == str(_CLAUDE_BIND)
        ),
        default=-1,
    )
    assert claude_bind_idx >= 0, "expected a ~/.claude bind in default wrap"
    tmpfs_idx = next(
        i
        for i, t in enumerate(out)
        if t == "--tmpfs" and out[i + 1] == str(_PHASE_CHAIN)
    )
    assert tmpfs_idx > claude_bind_idx, (
        f"phase-chain tmpfs (idx {tmpfs_idx}) must come AFTER the ~/.claude "
        f"overlay bind (idx {claude_bind_idx}) or it is silently shadowed"
    )


@pytest.mark.skipif(not _ENV_FILE.is_file(), reason="888 config/.env not present")
def test_env_ro_bind_present_and_post_overlay(tmp_path: Path) -> None:
    """config/.env is re-bound read-only AFTER the overlay (S2 closure)."""
    out = _wrap(tmp_path)
    ro_idx = next(
        (
            i
            for i, t in enumerate(out)
            if t == "--ro-bind"
            and out[i + 1] == str(_ENV_FILE)
            and out[i + 2] == str(_ENV_FILE)
        ),
        -1,
    )
    assert ro_idx >= 0, "expected read-only bind of 888 config/.env"
    claude_bind_idx = max(
        i
        for i, t in enumerate(out)
        if t == "--bind" and out[i + 1] == str(_CLAUDE_BIND)
    )
    assert ro_idx > claude_bind_idx, "env ro-bind must come after the overlay bind"


# ── Real-bwrap PoC ───────────────────────────────────────────────────────────


@requires_bwrap
def test_real_worker_verdict_write_invisible_on_host(tmp_path: Path) -> None:
    """A worker write into verdict-dir lands in tmpfs and is INVISIBLE on the host
    (S1 forge defeated at the OS layer)."""
    marker = _PHASE_CHAIN / "verdicts" / "fs10-forged-marker.json"
    # Defensive: ensure no stale marker, and clean up whatever the run leaves.
    if marker.exists():
        marker.unlink()
    wrapped = BwrapSandbox().wrap_command(
        [
            "bash",
            "-c",
            f"mkdir -p {marker.parent} && "
            f'echo \'{{"verdict":"PASS","forged":true}}\' > {marker} && '
            "echo wrote=$?",
        ],
        worktree=tmp_path,
        env={"PATH": "/usr/bin:/bin", "HOME": str(_HOME)},
    )
    try:
        res = subprocess.run(
            wrapped, capture_output=True, text=True, timeout=15.0, check=False
        )
        if "Creating new namespace failed" in res.stderr:
            pytest.skip(f"host namespace exhaustion: {res.stderr.strip()}")
        # The in-sandbox write itself succeeds (tmpfs is writable) ...
        assert "wrote=0" in (res.stdout + res.stderr), res.stdout + res.stderr
        # ... but it is DISCARDED: nothing reaches the host verdict-dir.
        assert not marker.exists(), (
            "worker write to verdict-dir leaked to host — tmpfs blackout failed "
            "(S1 forge channel still open)"
        )
    finally:
        if marker.exists():
            marker.unlink()


@requires_bwrap
def test_real_worker_cannot_flip_enforce_flag(tmp_path: Path) -> None:
    """A worker append to config/.env is rejected by the read-only bind (S2)."""
    if not _ENV_FILE.is_file():
        pytest.skip("888 config/.env not present")
    before = _ENV_FILE.read_bytes()
    wrapped = BwrapSandbox().wrap_command(
        [
            "bash",
            "-c",
            f"echo 'BMAD_888_PHASE_CHAIN_ENFORCE=off  # PWNED' >> {_ENV_FILE}; "
            "echo rc=$?",
        ],
        worktree=tmp_path,
        env={"PATH": "/usr/bin:/bin", "HOME": str(_HOME)},
    )
    try:
        res = subprocess.run(
            wrapped, capture_output=True, text=True, timeout=15.0, check=False
        )
        if "Creating new namespace failed" in res.stderr:
            pytest.skip(f"host namespace exhaustion: {res.stderr.strip()}")
        combined = res.stdout + res.stderr
        # Append must fail (read-only filesystem) — rc != 0.
        assert "rc=0" not in combined.splitlines()[-1], (
            f"worker append to .env succeeded — ro-bind failed (S2 open):\n{combined}"
        )
    finally:
        # Belt-and-suspenders: if the ro-bind ever regressed, restore the host file.
        if _ENV_FILE.read_bytes() != before:
            _ENV_FILE.write_bytes(before)
            pytest.fail("host .env was modified — ro-bind regressed; restored")
