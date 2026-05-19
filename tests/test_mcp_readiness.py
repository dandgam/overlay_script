"""Tests for MCP server readiness polling (Initiative pilot_findings_closure S5).

Spec section: spec/spec_pilot_findings_closure.md §1 #5 (R2).

Layout (target +8 tests):

* 4 unit — :func:`poll_mcp_ready` polling semantics (immediate ready,
  slow auth, never ready timeout, partial-set timeout).
* 2 unit — :func:`parse_mcp_list_output` accepts both shapes (``servers``
  wrapped dict + bare list) and rejects malformed JSON.
* 2 integration — :func:`runtime.worker_spawn.spawn_worker` aborts with
  :class:`MCPNotReadyError` + emits ``mcp_not_ready`` JSONL event when a
  required tool is missing, and proceeds normally when all tools ready.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from bmad_orchestrator.agent.tools._common import worker_jsonl_path
from bmad_orchestrator.runtime.mcp_readiness import (
    ReadinessResult,
    parse_mcp_list_output,
    poll_mcp_ready,
)
from bmad_orchestrator.runtime.worker_spawn import (
    MCPNotReadyError,
    spawn_worker,
)

# ── 1) Unit — poll_mcp_ready polling semantics ────────────────────────────────


@pytest.mark.asyncio
async def test_poll_immediately_ready_no_subprocess_when_required_empty() -> None:
    """Empty required_tools short-circuits — no subprocess, ok=True, polls=0."""
    res = await poll_mcp_ready([], timeout_s=30)
    assert res.ok is True
    assert res.missing == []
    assert res.polls == 0
    assert res.last_error is None


@pytest.mark.asyncio
async def test_poll_slow_auth_eventually_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """A tool that becomes authenticated on the 3rd poll → ok=True."""
    poll_count = {"n": 0}

    async def _fake_run(_argv: Sequence[str]) -> tuple[str, str | None]:
        poll_count["n"] += 1
        if poll_count["n"] < 3:
            return (
                json.dumps({"servers": [{"name": "analyzer", "authenticated": False}]}),
                None,
            )
        return (
            json.dumps({"servers": [{"name": "analyzer", "authenticated": True}]}),
            None,
        )

    monkeypatch.setattr(
        "bmad_orchestrator.runtime.mcp_readiness._run_mcp_list", _fake_run
    )

    async def _no_sleep(_seconds: float) -> None:
        return None

    res = await poll_mcp_ready(
        ["analyzer"],
        timeout_s=10,
        interval_ms=10,
        cli_argv=["claude", "mcp", "list", "--json"],
        sleep=_no_sleep,
    )
    assert res.ok is True
    assert res.missing == []
    assert res.polls == 3
    assert res.last_error is None


@pytest.mark.asyncio
async def test_poll_never_ready_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """A tool that never authenticates → ok=False, missing=[tool], polls≥2."""
    async def _fake_run(_argv: Sequence[str]) -> tuple[str, str | None]:
        return (
            json.dumps({"servers": [{"name": "analyzer", "authenticated": False}]}),
            None,
        )

    monkeypatch.setattr(
        "bmad_orchestrator.runtime.mcp_readiness._run_mcp_list", _fake_run
    )

    # Fake clock advances by interval each sleep so we exit the loop
    # deterministically after a few polls.
    state = {"now": 0.0}

    def _clock() -> float:
        return state["now"]

    async def _sleep(seconds: float) -> None:
        state["now"] += seconds

    res = await poll_mcp_ready(
        ["analyzer"],
        timeout_s=1,
        interval_ms=400,
        cli_argv=["claude", "mcp", "list", "--json"],
        clock=_clock,
        sleep=_sleep,
    )
    assert res.ok is False
    assert res.missing == ["analyzer"]
    assert res.polls >= 2
    assert res.elapsed_ms >= 1000


@pytest.mark.asyncio
async def test_poll_partial_set_reports_only_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two required, one authenticated, one missing → ok=False, missing=[other]."""
    async def _fake_run(_argv: Sequence[str]) -> tuple[str, str | None]:
        return (
            json.dumps(
                {
                    "servers": [
                        {"name": "analyzer", "authenticated": True},
                        {"name": "postgres-mcp", "authenticated": False},
                    ]
                }
            ),
            None,
        )

    monkeypatch.setattr(
        "bmad_orchestrator.runtime.mcp_readiness._run_mcp_list", _fake_run
    )

    state = {"now": 0.0}

    def _clock() -> float:
        return state["now"]

    async def _sleep(seconds: float) -> None:
        state["now"] += seconds

    res = await poll_mcp_ready(
        ["analyzer", "postgres-mcp"],
        timeout_s=1,
        interval_ms=500,
        cli_argv=["claude", "mcp", "list", "--json"],
        clock=_clock,
        sleep=_sleep,
    )
    assert res.ok is False
    assert res.missing == ["postgres-mcp"]
    assert res.polls >= 2


# ── 2) Unit — parse_mcp_list_output shape tolerance ───────────────────────────


def test_parse_mcp_list_output_handles_wrapped_and_bare_shapes() -> None:
    """Both ``{"servers": [...]}`` and bare list yield identical mappings."""
    wrapped = json.dumps(
        {
            "servers": [
                {"name": "analyzer", "authenticated": True},
                {"name": "postgres-mcp", "authenticated": False},
            ]
        }
    )
    bare = json.dumps(
        [
            {"name": "analyzer", "authenticated": True},
            {"name": "postgres-mcp", "authenticated": False},
        ]
    )
    expected = {"analyzer": True, "postgres-mcp": False}
    assert parse_mcp_list_output(wrapped) == expected
    assert parse_mcp_list_output(bare) == expected


def test_parse_mcp_list_output_rejects_malformed_json_safely() -> None:
    """Empty stdout / unparseable JSON / unexpected shape → empty mapping."""
    assert parse_mcp_list_output("") == {}
    assert parse_mcp_list_output("not json at all") == {}
    # Unexpected shape (object without servers, not a list)
    assert parse_mcp_list_output('{"unrelated": 1}') == {}
    # Entry missing name / authenticated treated as missing.
    half = json.dumps([{"name": "x"}, {"authenticated": True}])
    assert parse_mcp_list_output(half) == {"x": False}


# ── 3) Integration — spawn_worker MCP gating ──────────────────────────────────


@pytest.mark.asyncio
async def test_spawn_worker_aborts_when_required_mcp_not_ready(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """spawn_worker raises MCPNotReadyError + emits mcp_not_ready JSONL when missing."""
    async def _fake_poll(
        required_tools: Sequence[str], **_kwargs: object
    ) -> ReadinessResult:
        return ReadinessResult(
            ok=False,
            missing=list(required_tools),
            elapsed_ms=30000,
            polls=60,
            last_error=None,
        )

    monkeypatch.setattr(
        "bmad_orchestrator.runtime.worker_spawn.poll_mcp_ready", _fake_poll
    )

    # Truncate any stale events.jsonl at the resolved path (basename derived
    # from tmp_path so cross-test bleed is unlikely; cross-run residue from
    # prior pytest invocations is the realistic concern).
    pre_jsonl = worker_jsonl_path(str(tmp_path))
    if pre_jsonl.exists():
        pre_jsonl.write_text("", encoding="utf-8")

    with pytest.raises(MCPNotReadyError) as excinfo:
        await spawn_worker(
            worktree=str(tmp_path),
            story_id="1.5",
            branch="feature/1-5",
            mock=True,
            required_mcp_tools=["analyzer"],
        )
    err = excinfo.value
    assert err.story_id == "1.5"
    assert err.missing == ["analyzer"]
    assert err.elapsed_ms == 30000

    # JSONL audit line landed.
    jsonl = worker_jsonl_path(str(tmp_path))
    assert jsonl.exists(), f"expected events.jsonl at {jsonl}"
    lines = [json.loads(line) for line in jsonl.read_text().splitlines() if line]
    halt = [ev for ev in lines if ev.get("event_type") == "mcp_not_ready"]
    assert len(halt) == 1, halt
    assert halt[0]["story_id"] == "1.5"
    assert halt[0]["missing"] == ["analyzer"]
    assert halt[0]["required"] == ["analyzer"]
    # No worker_spawned event — we halted before spawn.
    assert not [ev for ev in lines if ev.get("event_type") == "worker_spawned"]


@pytest.mark.asyncio
async def test_spawn_worker_proceeds_when_required_mcp_ready(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When poll_mcp_ready returns ok=True, spawn_worker (mock-mode) completes."""
    async def _fake_poll(
        required_tools: Sequence[str], **_kwargs: object
    ) -> ReadinessResult:
        return ReadinessResult(
            ok=True, missing=[], elapsed_ms=500, polls=1, last_error=None
        )

    monkeypatch.setattr(
        "bmad_orchestrator.runtime.worker_spawn.poll_mcp_ready", _fake_poll
    )

    pre_jsonl = worker_jsonl_path(str(tmp_path))
    if pre_jsonl.exists():
        pre_jsonl.write_text("", encoding="utf-8")

    handle = await spawn_worker(
        worktree=str(tmp_path),
        story_id="1.5",
        branch="feature/1-5",
        mock=True,
        required_mcp_tools=["analyzer"],
    )
    assert handle.mock is True
    assert handle.story_id == "1.5"

    jsonl = worker_jsonl_path(str(tmp_path))
    lines = [json.loads(line) for line in jsonl.read_text().splitlines() if line]
    # No halt — we proceeded.
    assert not [ev for ev in lines if ev.get("event_type") == "mcp_not_ready"]
    # Worker spawned event present.
    assert [ev for ev in lines if ev.get("event_type") == "worker_spawned"]


# Sanity — keep asyncio runner deterministic if invoked standalone.
if __name__ == "__main__":  # pragma: no cover
    asyncio.run(test_poll_immediately_ready_no_subprocess_when_required_empty())
