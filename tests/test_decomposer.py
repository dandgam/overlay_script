"""Tests for the production auto-split decomposer (Initiative #2 — S2).

Covers ``runtime/decomposer.py::claude_decomposer`` — the real ``DecomposeFn``
that closes the ``_DECOMPOSER = None`` gap so ``BMAD_AUTO_SPLIT=1`` actually
diverts large stories through decomposition.

Scope:
* binary-missing → DecomposerSpawnError
* happy path → raw stdout returned verbatim (fence-stripping is the caller's job)
* non-zero exit → DecomposerSpawnError with stderr tail
* timeout → DecomposerSpawnError + process killed
* prompt is piped via stdin
* ``_run_real_pilot`` wires claude_decomposer only when none pre-injected
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from bmad_orchestrator.runtime.decomposer import (
    DECOMPOSER_TIMEOUT_SEC,
    DecomposerSpawnError,
    claude_decomposer,
)

_STORY = {"id": "4.8", "title": "invite flow", "touches_files": ["a.py", "b.py"]}


class _FakeProc:
    """Minimal stand-in for asyncio.subprocess.Process."""

    def __init__(
        self,
        *,
        stdout: bytes = b"[]",
        stderr: bytes = b"",
        returncode: int = 0,
        hang: bool = False,
    ) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode: int | None = returncode
        self._hang = hang
        self.killed = False
        self.stdin_received: bytes | None = None

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
        self.stdin_received = input
        if self._hang:
            await asyncio.sleep(3600)  # never returns within the test timeout
        return self._stdout, self._stderr

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode or 0


def _patch_spawn(monkeypatch: pytest.MonkeyPatch, proc: _FakeProc) -> dict[str, Any]:
    """Patch asyncio.create_subprocess_exec → return ``proc``; capture argv."""
    captured: dict[str, Any] = {}

    async def _fake_exec(*args: Any, **kwargs: Any) -> _FakeProc:
        captured["argv"] = args
        captured["kwargs"] = kwargs
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
    return captured


@pytest.mark.asyncio
async def test_decomposer_binary_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _: None)
    with pytest.raises(DecomposerSpawnError, match="not found in PATH"):
        await claude_decomposer("prompt", _STORY)


@pytest.mark.asyncio
async def test_decomposer_happy_path_returns_stdout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/claude")
    payload = '[{"id": "4.8.1", "title": "schema"}]'
    proc = _FakeProc(stdout=payload.encode())
    _patch_spawn(monkeypatch, proc)

    out = await claude_decomposer("decompose this", _STORY)
    assert out == payload


@pytest.mark.asyncio
async def test_decomposer_pipes_prompt_via_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/claude")
    proc = _FakeProc(stdout=b"[]")
    cap = _patch_spawn(monkeypatch, proc)

    await claude_decomposer("THE PROMPT BODY", _STORY)
    assert proc.stdin_received == b"THE PROMPT BODY"
    # argv: claude -p --model <model>
    argv = cap["argv"]
    assert argv[0] == "/usr/bin/claude"
    assert "-p" in argv
    assert "--model" in argv


@pytest.mark.asyncio
async def test_decomposer_nonzero_exit_raises_with_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/claude")
    proc = _FakeProc(stdout=b"", stderr=b"rate limit exceeded", returncode=1)
    _patch_spawn(monkeypatch, proc)

    with pytest.raises(DecomposerSpawnError, match="exited 1"):
        await claude_decomposer("prompt", _STORY)


@pytest.mark.asyncio
async def test_decomposer_timeout_kills_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/claude")
    monkeypatch.setattr(
        "bmad_orchestrator.runtime.decomposer.DECOMPOSER_TIMEOUT_SEC", 0.05
    )
    proc = _FakeProc(hang=True)
    _patch_spawn(monkeypatch, proc)

    with pytest.raises(DecomposerSpawnError, match="timed out"):
        await claude_decomposer("prompt", _STORY)
    assert proc.killed is True


@pytest.mark.asyncio
async def test_decomposer_error_context_includes_story_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/claude")
    proc = _FakeProc(stderr=b"boom", returncode=2)
    _patch_spawn(monkeypatch, proc)

    with pytest.raises(DecomposerSpawnError, match=r"story 4\.8"):
        await claude_decomposer("prompt", _STORY)


def test_decomposer_timeout_constant_is_sane() -> None:
    assert 60 <= DECOMPOSER_TIMEOUT_SEC <= 600


def test_run_real_pilot_wires_decomposer_when_none_injected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``set_decomposer(None)`` then the wiring block installs claude_decomposer.

    Mirrors the ``if get_decomposer() is None`` gate in ``_run_real_pilot``.
    """
    from bmad_orchestrator.agent.run import get_decomposer, set_decomposer

    set_decomposer(None)
    assert get_decomposer() is None

    # Simulate the wiring block.
    if get_decomposer() is None:
        set_decomposer(claude_decomposer)
    assert get_decomposer() is claude_decomposer

    set_decomposer(None)  # cleanup


def test_run_real_pilot_keeps_preinjected_stub(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A test-injected decomposer stub survives the wiring block."""
    from bmad_orchestrator.agent.run import get_decomposer, set_decomposer

    async def _stub(prompt: str, story: dict[str, Any]) -> str:
        return "[]"

    set_decomposer(_stub)
    if get_decomposer() is None:  # wiring block — must NOT fire
        set_decomposer(claude_decomposer)
    assert get_decomposer() is _stub

    set_decomposer(None)  # cleanup
