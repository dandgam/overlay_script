"""FS6 cleanup tests — N1..N7 from spec_orchestrator_agent_security_fixes_2.md §5.6.

Each block validates the corresponding cleanup fix:

* **N1** — circular import broken: ``import bmad_orchestrator.runtime.worker_spawn``
  must exit 0 with no ``ImportError``. ``DEFAULT_MODEL`` /
  ``DEFAULT_BUDGET_CAP_USD`` now live in :mod:`bmad_orchestrator.config`.
* **N2** — daily cap halts mock pilot after the projected spend would cross
  ``BMAD_DAILY_LIMIT_USD``.
* **N3** — ``run_orchestrator`` default is ``mock=True`` so the CLI never
  silently raises ``NotImplementedError`` without ``--real`` opt-in.
* **N4** — ``detect_wave_boundary`` rejects seed-stub retros (~100 chars,
  frontmatter-only) instead of trusting ``st_size > 0``.
* **N5** — ``gh_or_curl`` urllib fallback rejects non-allowlisted hosts
  (``file://``, localhost, evil.com) before the bearer token is built.
* **N6** — ``bot.main._attach_bridge`` wires StateDB → ``attach_state_db``
  and falls back to stub mode on any DB error.
* **N7** — ``spawn_retro_worktree(real=True)`` runs the subprocess with the
  worker_spawn allow-list env, scrubbing ``ANTHROPIC_API_KEY`` /
  ``GITHUB_TOKEN`` etc.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import subprocess
import sys
from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# N1 — circular import fix
# ─────────────────────────────────────────────────────────────────────────────


def test_n1_worker_spawn_imports_cleanly() -> None:
    """``import bmad_orchestrator.runtime.worker_spawn`` must succeed in a
    fresh subprocess (no prior module cache)."""
    result = subprocess.run(
        [sys.executable, "-c", "import bmad_orchestrator.runtime.worker_spawn"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, (
        f"circular import regressed: stderr={result.stderr!r}"
    )
    assert "ImportError" not in result.stderr
    assert "circular" not in result.stderr.lower()


def test_n1_constants_live_in_config() -> None:
    """``DEFAULT_MODEL`` and ``DEFAULT_BUDGET_CAP_USD`` must be canonical in
    :mod:`bmad_orchestrator.config`."""
    from bmad_orchestrator import config

    assert config.DEFAULT_MODEL == "claude-sonnet-4-6"
    assert config.DEFAULT_BUDGET_CAP_USD == 30.0


def test_n1_worker_spawn_reexports_constants() -> None:
    """Back-compat: ``runtime.worker_spawn`` still exports the constants for
    callers that imported them at the old path before FS6."""
    from bmad_orchestrator.runtime import worker_spawn

    assert worker_spawn.DEFAULT_MODEL == "claude-sonnet-4-6"
    assert worker_spawn.DEFAULT_BUDGET_CAP_USD == 30.0


# ─────────────────────────────────────────────────────────────────────────────
# N2 — daily cap wiring
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_n2_daily_limit_halts_mock_pilot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With ``BMAD_DAILY_LIMIT_USD=$10`` and each story projecting $5 spend,
    the mock pilot must stop after the projected daily total exceeds $10."""
    monkeypatch.setenv("BMAD_DAILY_LIMIT_USD", "10")

    from bmad_orchestrator.agent.run import run_orchestrator
    from bmad_orchestrator.config import load_settings
    from bmad_orchestrator.runtime.event_loop import EventLoop, EventType

    # Point target_project at a tmp dir with a minimal sprint-status.yaml.
    target = tmp_path / "proj"
    target.mkdir()
    artifacts = target / "_bmad-output" / "planning-artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "sprint-status.yaml").write_text(
        "wave: w\n"
        "epics:\n"
        "  e1:\n"
        "    stories:\n"
        "      s1: ready-for-dev\n"
        "      s2: ready-for-dev\n"
        "      s3: ready-for-dev\n"
        "      s4: ready-for-dev\n"
        "      s5: ready-for-dev\n",
        encoding="utf-8",
    )
    stories_dir = artifacts / "stories"
    stories_dir.mkdir()
    for sid in ("s1", "s2", "s3", "s4", "s5"):
        (stories_dir / f"{sid}.md").write_text(
            f"# Story {sid}\n\n"
            "- **epic:** 1\n"
            "- **status:** ready\n"
            "- **risk:** low\n"
            "- **estimated_tokens:** 1000\n"
            "- **estimated_minutes:** 5\n"
            "- **touches_files:** []\n"
            "- **touches_shared:** []\n"
            "- **depends_on:** []\n",
            encoding="utf-8",
        )

    monkeypatch.setenv("ORCHESTRATOR_TARGET_PROJECT", str(target))
    load_settings.cache_clear() if hasattr(load_settings, "cache_clear") else None

    bus = EventLoop()
    halt_events: list[dict[str, object]] = []

    async def capture_halt(event: object) -> None:
        if getattr(event, "type", None) == EventType.BUDGET_THRESHOLD_HIT:
            halt_events.append(dict(getattr(event, "payload", {}) or {}))

    bus.on(capture_halt)

    await run_orchestrator(
        project="proj",
        wave="w",
        max_parallel=1,
        mock=True,
        event_loop=bus,
    )

    # Drain queued events so subscribers fire.
    while await bus.dispatch_one(timeout=0.01) is not None:
        pass

    daily_halts = [e for e in halt_events if e.get("scope") == "day"]
    assert daily_halts, (
        f"expected at least one day-scope halt event, got {halt_events!r}"
    )


def test_n2_run_orchestrator_reads_env_var() -> None:
    """``_run_mock_pilot`` source must reference ``BMAD_DAILY_LIMIT_USD`` and
    ``enforce_day`` so the wiring is visible at grep-time."""
    from bmad_orchestrator.agent import run as run_module

    src = inspect.getsource(run_module._run_mock_pilot)
    assert "BMAD_DAILY_LIMIT_USD" in src
    assert "enforce_day" in src


# ─────────────────────────────────────────────────────────────────────────────
# N3 — mock default = True
# ─────────────────────────────────────────────────────────────────────────────


def test_n3_run_orchestrator_default_is_mock() -> None:
    from bmad_orchestrator.agent.run import run_orchestrator

    sig = inspect.signature(run_orchestrator)
    assert sig.parameters["mock"].default is True


def test_n3_cli_run_flag_default_is_mock() -> None:
    """The Typer CLI's ``--mock/--real`` flag must default to ``mock=True``."""
    from bmad_orchestrator.cli import main as cli_main

    sig = inspect.signature(cli_main.run)
    assert sig.parameters["mock"].default.default is True, (
        f"unexpected default: {sig.parameters['mock'].default!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# N4 — detect_wave_boundary content-schema check
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_n4_seed_stub_retro_does_not_count_as_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A frontmatter-only seed retro (~100 chars) must produce ``retro_done=False``.

    The pre-N4 implementation read ``st_size > 0`` and silently passed.
    """
    from bmad_orchestrator.agent.tools.retro import detect_wave_boundary

    target = tmp_path / "proj"
    target.mkdir()
    artifacts = target / "_bmad-output" / "planning-artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "sprint-status.yaml").write_text(
        "wave: w\n"
        "epics:\n"
        "  e1:\n"
        "    stories:\n"
        "      s1: done\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("ORCHESTRATOR_TARGET_PROJECT", str(target))
    monkeypatch.setenv("ORCHESTRATOR_ORCHESTRATOR_HOME", str(tmp_path / "home"))

    # Drop a seed-stub retro (~100 chars, frontmatter-only).
    retro_dir = tmp_path / "home" / ".claude" / "memory" / "per-wave"
    retro_dir.mkdir(parents=True)
    (retro_dir / "w-retrospective.md").write_text(
        "---\nwave: w\nlevel: wave\ncreated: 2026-05-16\n---\n\n"
        "# Retrospective seed\n\n"
        "_TODO: agent must fill from per-story lessons._\n",
        encoding="utf-8",
    )

    res = await detect_wave_boundary.handler({"wave": "w"})
    import json as _json
    payload = _json.loads(res["content"][0]["text"])
    assert payload["retro_done"] is False, payload
    assert payload["complete"] is False, payload


# ─────────────────────────────────────────────────────────────────────────────
# N5 — gh_or_curl SSRF guard
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def _no_gh(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    """Pretend the gh CLI is unavailable so the urllib fallback runs."""
    monkeypatch.setattr(
        "bmad_orchestrator.imports.from_bad.gh_client.shutil.which",
        lambda _name: None,
    )
    monkeypatch.setenv("GH_TOKEN", "ghp_dummy_token")
    yield


def test_n5_file_scheme_blocked(_no_gh: None) -> None:
    from bmad_orchestrator.imports.from_bad.gh_client import gh_or_curl

    with pytest.raises(ValueError, match="ssrf_blocked"):
        gh_or_curl(["api", "x"], curl_url="file:///etc/passwd")


def test_n5_localhost_blocked(_no_gh: None) -> None:
    from bmad_orchestrator.imports.from_bad.gh_client import gh_or_curl

    with pytest.raises(ValueError, match="ssrf_blocked"):
        gh_or_curl(["api", "x"], curl_url="http://localhost:8080/admin")


def test_n5_http_scheme_blocked(_no_gh: None) -> None:
    """Even GitHub hostnames over plain HTTP must be rejected."""
    from bmad_orchestrator.imports.from_bad.gh_client import gh_or_curl

    with pytest.raises(ValueError, match=r"ssrf_blocked.*https"):
        gh_or_curl(["api", "x"], curl_url="http://api.github.com/")


def test_n5_evil_host_blocked(_no_gh: None) -> None:
    from bmad_orchestrator.imports.from_bad.gh_client import gh_or_curl

    with pytest.raises(ValueError, match="ssrf_blocked"):
        gh_or_curl(["api", "x"], curl_url="https://evil.com/exfil")


def test_n5_github_api_passes_validation(_no_gh: None) -> None:
    """A canonical api.github.com URL must NOT raise — the SSRF guard lets it
    through and any failure must come from the HTTP layer (not the guard).
    """
    from bmad_orchestrator.imports.from_bad import gh_client

    # Patch the actual urlopen to avoid real network.
    class _FakeResp:
        def __enter__(self) -> _FakeResp:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"ok": true}'

    with patch.object(gh_client.urllib.request, "urlopen", return_value=_FakeResp()):
        rc, body = gh_client.gh_or_curl(
            ["api", "x"], curl_url="https://api.github.com/repos/x/y"
        )
    assert rc == 0
    assert "ok" in body


def test_n5_allowed_hosts_frozenset_immutable() -> None:
    from bmad_orchestrator.imports.from_bad.gh_client import ALLOWED_GH_HOSTS

    assert isinstance(ALLOWED_GH_HOSTS, frozenset)
    assert "api.github.com" in ALLOWED_GH_HOSTS
    assert "evil.com" not in ALLOWED_GH_HOSTS


# ─────────────────────────────────────────────────────────────────────────────
# N6 — bot/main attach_state_db
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_n6_attach_bridge_wires_state_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``_attach_bridge`` must call ``attach_state_db`` with a real DB session."""
    monkeypatch.setenv("ORCHESTRATOR_STATE_DB", str(tmp_path / "state.db"))

    from bmad_orchestrator.bot import handlers
    from bmad_orchestrator.bot.main import _attach_bridge

    # Reset any prior binding.
    handlers.attach_state_db(None, None)

    await _attach_bridge()

    assert handlers._STATE_DB is not None
    assert handlers._SESSION_ID is not None

    # Teardown for test isolation.
    handlers.attach_state_db(None, None)


@pytest.mark.asyncio
async def test_n6_attach_bridge_falls_back_to_stub_on_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A bogus DB path (read-only parent) must NOT crash bot startup; it must
    log a warning and leave the handler in stub mode."""
    from bmad_orchestrator.bot import handlers
    from bmad_orchestrator.bot.main import _attach_bridge

    handlers.attach_state_db(None, None)

    # Point at an unwritable path.
    monkeypatch.setenv(
        "ORCHESTRATOR_STATE_DB",
        "/proc/cannot-write-here/state.db",
    )

    # Should not raise.
    await _attach_bridge()

    # Stub mode preserved.
    assert handlers._STATE_DB is None


# ─────────────────────────────────────────────────────────────────────────────
# N7 — retro spawn env allowlist
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_n7_spawn_retro_strips_secrets_from_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``spawn_retro_worktree(real=True)`` must pass an allow-listed env to the
    subprocess — secrets like ``ANTHROPIC_API_KEY`` must NOT be inherited.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-leak-payload")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_should_not_leak")
    monkeypatch.setenv("PATH", os.environ.get("PATH", "/usr/bin"))

    captured_env: dict[str, str] = {}

    class _FakeProc:
        pid = 12345

        async def wait(self) -> int:
            return 0

        def kill(self) -> None:  # pragma: no cover — unused
            pass

    async def fake_create_subprocess_exec(
        *_args: object, env: dict[str, str] | None = None, **_kwargs: object
    ) -> _FakeProc:
        if env is not None:
            captured_env.update(env)
        return _FakeProc()

    monkeypatch.setattr(
        "bmad_orchestrator.agent.tools.retro.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    monkeypatch.setattr(
        "bmad_orchestrator.agent.tools.retro.shutil.which",
        lambda _name: "/usr/bin/fake-claude",
    )

    # Stub _retro_path_for so the file write is benign.
    monkeypatch.setattr(
        "bmad_orchestrator.agent.tools.retro._retro_path_for",
        lambda _wave, _level: tmp_path / "retro.md",
    )

    from bmad_orchestrator.agent.tools.retro import spawn_retro_worktree

    result = await spawn_retro_worktree.handler({"wave": "1a", "level": "wave", "real": True})

    # Wait for the background task to settle.
    await asyncio.sleep(0.05)

    # Result indicates real-mode spawn.
    import json as _json
    payload = _json.loads(result["content"][0]["text"])
    assert payload["mock"] is False
    assert payload["pid"] == 12345

    # Secret env vars must NOT be present in the subprocess env.
    assert "ANTHROPIC_API_KEY" not in captured_env, (
        f"secret leaked into subprocess env: {captured_env!r}"
    )
    assert "GITHUB_TOKEN" not in captured_env
    # Allow-listed entries should be present.
    assert "PATH" in captured_env
    # Caller-passed context vars should pass through.
    assert captured_env.get("ORCHESTRATOR_WAVE") == "1a"
    assert captured_env.get("ORCHESTRATOR_LEVEL") == "wave"
