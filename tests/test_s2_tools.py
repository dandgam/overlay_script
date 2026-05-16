"""S2 acceptance tests — Tools layer (spec §17).

Coverage:
- All 35 tools registered (5 state + 3 DAG + 4 spawn + 4 control + 3 merge
  + 3 memory + 3 retro + 5 operational + 2 splitter + 2 escalate + 1 audit).
- Each tool is invokable with a valid mock argument set and returns a structured
  MCP content payload (not an error) unless explicitly testing error path.
- Tool Search Tool beta header is in the canonical beta_headers list (S1 already
  asserted it; we re-assert here to keep the S2 regression matrix complete).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _isolate_target_project(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Copy mock-odyssey to tmp_path so each test gets an isolated target project."""
    import shutil

    src = Path(__file__).parent / "fixtures" / "mock-odyssey"
    dst = tmp_path / "mock-odyssey"
    shutil.copytree(src, dst)
    monkeypatch.setenv("ORCHESTRATOR_TARGET_PROJECT", str(dst))
    monkeypatch.setenv("ORCHESTRATOR_STATE_DB", str(tmp_path / "state.db"))
    monkeypatch.setenv("ORCHESTRATOR_ORCHESTRATOR_HOME", str(tmp_path / "orch"))


async def _call(tool_obj: Any, args: dict[str, Any]) -> dict[str, Any]:
    """Invoke an SdkMcpTool.handler and decode the JSON text reply if any."""
    result = await tool_obj.handler(args)
    assert isinstance(result, dict), f"expected dict reply, got {type(result)}"
    return result


def _payload(reply: dict[str, Any]) -> Any:
    body = reply["content"][0]["text"]
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return body


def _is_error(reply: dict[str, Any]) -> bool:
    return bool(reply.get("isError"))


# ── Catalog smoke tests ──────────────────────────────────────────────────────


def test_all_tools_registered() -> None:
    from bmad_orchestrator.agent.tools import ALL_TOOLS, tool_names

    names = tool_names()
    assert len(ALL_TOOLS) == 35, f"expected 35 tools, got {len(ALL_TOOLS)}"
    # Spec §17 names must be present:
    required = {
        "read_sprint_status",
        "list_worktrees",
        "get_worker_status",
        "tail_worker_jsonl",
        "get_budget",
        "build_dag",
        "find_ready_stories",
        "predict_conflicts",
        "create_worktree",
        "spawn_worker",
        "sync_skill_patches",
        "cleanup_worktree",
        "pause_worker",
        "resume_worker",
        "respond_to_elicitation",
        "spawn_fixer",
        "run_code_review",
        "run_security_review",
        "git_merge",
        "read_memory",
        "write_memory",
        "compress_wave_lessons",
        "spawn_retro_worktree",
        "start_wave",
        "stop_orchestrator",
        "set_model",
        "schedule_reminder",
        "check_should_split",
        "split_story",
        "escalate_to_human",
        "update_sprint_status",
    }
    missing = required - set(names)
    assert not missing, f"missing tools: {missing!r}"


def test_tool_search_tool_beta_header_present() -> None:
    from bmad_orchestrator.agent.betas import ANTHROPIC_BETA_HEADERS

    assert "tool-search-tool-2025-10-19" in ANTHROPIC_BETA_HEADERS


# ── State (5) ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_state_read_sprint_status() -> None:
    from bmad_orchestrator.agent.tools.state import read_sprint_status

    r = await _call(read_sprint_status, {"project": "mock-odyssey"})
    assert not _is_error(r)
    p = _payload(r)
    assert p["sprint_status"]["wave"] == "1a"
    assert "1" in p["sprint_status"]["epics"]


@pytest.mark.asyncio
async def test_state_list_worktrees_empty() -> None:
    from bmad_orchestrator.agent.tools.state import list_worktrees

    r = await _call(list_worktrees, {})
    assert not _is_error(r)
    p = _payload(r)
    assert "worktrees" in p and isinstance(p["worktrees"], list)


@pytest.mark.asyncio
async def test_state_get_worker_status_no_events() -> None:
    from bmad_orchestrator.agent.tools.state import get_worker_status

    r = await _call(get_worker_status, {"worktree": "/tmp/nonexistent"})
    assert not _is_error(r)
    p = _payload(r)
    assert p["events_seen"] == 0
    assert p["alive"] is False


@pytest.mark.asyncio
async def test_state_tail_worker_jsonl_missing() -> None:
    from bmad_orchestrator.agent.tools.state import tail_worker_jsonl

    r = await _call(tail_worker_jsonl, {"worktree": "/tmp/none", "n": 10})
    p = _payload(r)
    assert p["events"] == []


@pytest.mark.asyncio
async def test_state_get_budget_no_db() -> None:
    from bmad_orchestrator.agent.tools.state import get_budget

    r = await _call(get_budget, {"scope": "story"})
    p = _payload(r)
    assert p["rows"] == []


@pytest.mark.asyncio
async def test_state_get_budget_rejects_invalid_scope() -> None:
    from bmad_orchestrator.agent.tools.state import get_budget

    r = await _call(get_budget, {"scope": "bad"})
    assert _is_error(r)


# ── DAG (3) ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dag_build_for_wave_1a() -> None:
    from bmad_orchestrator.agent.tools.dag import build_dag

    r = await _call(build_dag, {"wave": "1a"})
    assert not _is_error(r)
    p = _payload(r)
    node_ids = [n["id"] for n in p["dag"]["nodes"]]
    assert "1-1-tenant-signup" in node_ids
    assert p["dag"]["node_count"] >= 1


@pytest.mark.asyncio
async def test_dag_find_ready_stories_returns_ready_for_dev() -> None:
    from bmad_orchestrator.agent.tools.dag import find_ready_stories

    r = await _call(find_ready_stories, {"max_n": 5})
    p = _payload(r)
    ids = [s["id"] for s in p["ready"]]
    assert "1-1-tenant-signup" in ids


@pytest.mark.asyncio
async def test_dag_predict_conflicts_overlapping_files() -> None:
    from bmad_orchestrator.agent.tools.dag import predict_conflicts

    r = await _call(
        predict_conflicts,
        {"story_ids": ["1-1-tenant-signup", "1-2-tenant-activate", "1-3-tenant-disable"]},
    )
    p = _payload(r)
    assert isinstance(p["conflicts"], list)


# ── Spawn (4) ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_spawn_create_worktree(tmp_path: Path) -> None:
    from bmad_orchestrator.agent.tools.spawn import (
        cleanup_worktree,
        create_worktree,
        spawn_worker,
        sync_skill_patches,
    )

    r = await _call(create_worktree, {"story_id": "1-1-tenant-signup", "branch": "feature/1-1"})
    p = _payload(r)
    wt = p["path"]
    assert os.path.exists(wt)

    r2 = await _call(spawn_worker, {"worktree": wt, "model": "claude-sonnet-4-6", "budget_cap_usd": 30.0})
    p2 = _payload(r2)
    assert p2["mock"] is True

    r3 = await _call(sync_skill_patches, {"worktree": wt})
    assert not _is_error(r3)

    r4 = await _call(cleanup_worktree, {"worktree": wt})
    assert not _is_error(r4)
    assert not os.path.exists(wt)


@pytest.mark.asyncio
async def test_spawn_create_worktree_rejects_existing(tmp_path: Path) -> None:
    from bmad_orchestrator.agent.tools.spawn import create_worktree

    args = {"story_id": "dup-story", "branch": "feature/dup"}
    await _call(create_worktree, args)
    r = await _call(create_worktree, args)
    assert _is_error(r)


# ── Control (4) ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_control_pause_resume_self_pid() -> None:
    from bmad_orchestrator.agent.tools.control import pause_worker, resume_worker

    pid = os.getpid()  # the test process itself is alive
    r = await _call(pause_worker, {"pid": pid, "real_signal": False})
    assert not _is_error(r)
    r2 = await _call(resume_worker, {"pid": pid, "real_signal": False})
    assert not _is_error(r2)


@pytest.mark.asyncio
async def test_control_respond_to_elicitation() -> None:
    from bmad_orchestrator.agent.tools.control import respond_to_elicitation

    r = await _call(respond_to_elicitation, {"pid": 12345, "answer": "yes"})
    assert not _is_error(r)


@pytest.mark.asyncio
async def test_control_spawn_fixer_records_findings() -> None:
    from bmad_orchestrator.agent.tools.control import spawn_fixer

    findings = [
        {"severity": "high", "category": "auth", "message": "missing rate limit"},
        {"severity": "medium", "category": "perf", "message": "n+1 query"},
    ]
    r = await _call(spawn_fixer, {"worktree": "/tmp/wt", "findings": findings})
    p = _payload(r)
    assert p["finding_count"] == 2
    assert "high" in p["severities"]


# ── Merge (3) ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_merge_run_code_review_passes_when_no_findings(tmp_path: Path) -> None:
    from bmad_orchestrator.agent.tools.merge import run_code_review

    r = await _call(run_code_review, {"worktree": str(tmp_path)})
    p = _payload(r)
    assert p["status"] == "pass"
    assert p["findings"] == []


@pytest.mark.asyncio
async def test_merge_run_security_review_fails_on_high_finding(tmp_path: Path) -> None:
    from bmad_orchestrator.agent.tools.merge import run_security_review

    (tmp_path / ".security_findings.json").write_text(
        json.dumps([{"severity": "critical", "category": "crypto", "message": "MD5 used"}]),
        encoding="utf-8",
    )
    r = await _call(run_security_review, {"worktree": str(tmp_path)})
    p = _payload(r)
    assert p["status"] == "fail"


@pytest.mark.asyncio
async def test_merge_git_merge_mock_when_not_a_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bmad_orchestrator.agent.safety.main_merge_token import generate_token
    from bmad_orchestrator.agent.tools.merge import git_merge

    monkeypatch.setenv(
        "BMAD_MAIN_MERGE_TOKEN_PATH", str(tmp_path / "main-merge-token.json")
    )
    token = generate_token(ttl_seconds=300)
    r = await _call(
        git_merge,
        {
            "worktree": str(tmp_path),
            "target_branch": "main",
            "message": "test",
            "signed_token": token,
        },
    )
    p = _payload(r)
    assert p["merged"] is True and p["mock"] is True


# ── Memory (3) ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_memory_write_then_read_round_trip() -> None:
    from bmad_orchestrator.agent.tools.memory import read_memory, write_memory

    await _call(
        write_memory,
        {"path": "per-story/1-1.md", "content": "Lesson A\n", "mode": "overwrite"},
    )
    r = await _call(read_memory, {"path": "per-story/1-1.md"})
    p = _payload(r)
    assert "Lesson A" in p["content"]


@pytest.mark.asyncio
async def test_memory_write_rejects_path_traversal() -> None:
    from bmad_orchestrator.agent.tools.memory import write_memory

    r = await _call(
        write_memory,
        {"path": "../../../etc/passwd", "content": "x", "mode": "overwrite"},
    )
    assert _is_error(r)


@pytest.mark.asyncio
async def test_memory_compress_wave_lessons() -> None:
    from bmad_orchestrator.agent.tools.memory import compress_wave_lessons, write_memory

    await _call(write_memory, {"path": "per-story/1a-foo.md", "content": "a", "mode": "overwrite"})
    await _call(write_memory, {"path": "per-story/1a-bar.md", "content": "b", "mode": "overwrite"})
    r = await _call(compress_wave_lessons, {"wave": "1a"})
    p = _payload(r)
    assert len(p["sources"]) == 2
    assert Path(p["out_path"]).exists()


# ── Retro (3) ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_retro_detect_wave_boundary_not_complete() -> None:
    from bmad_orchestrator.agent.tools.retro import detect_wave_boundary

    r = await _call(detect_wave_boundary, {"wave": "1a"})
    p = _payload(r)
    assert p["complete"] is False  # fixture has backlog/ready-for-dev statuses


@pytest.mark.asyncio
async def test_retro_spawn_creates_seed_file() -> None:
    from bmad_orchestrator.agent.tools.retro import spawn_retro_worktree

    r = await _call(spawn_retro_worktree, {"wave": "1a", "level": "wave"})
    p = _payload(r)
    assert Path(p["retrospective_path"]).exists()


@pytest.mark.asyncio
async def test_retro_gen_wave2_prd_draft_writes_skeleton() -> None:
    from bmad_orchestrator.agent.tools.retro import gen_wave2_prd_draft

    r = await _call(gen_wave2_prd_draft, {})
    p = _payload(r)
    assert Path(p["path"]).exists()


# ── Operational (5) ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_operational_start_wave_creates_session() -> None:
    from bmad_orchestrator.agent.tools.operational import start_wave

    r = await _call(start_wave, {"project": "mock-odyssey", "wave": "1a", "max_parallel": 2, "model": ""})
    p = _payload(r)
    assert p["session_id"] > 0
    assert p["wave"] == "1a"


@pytest.mark.asyncio
async def test_operational_stop_orchestrator_ends_sessions() -> None:
    from bmad_orchestrator.agent.tools.operational import start_wave, stop_orchestrator

    await _call(start_wave, {"project": "p", "wave": "w", "max_parallel": 2, "model": ""})
    r = await _call(stop_orchestrator, {"mode": "graceful"})
    p = _payload(r)
    assert p["sessions_ended"] >= 1


@pytest.mark.asyncio
async def test_operational_set_model_persists_to_config() -> None:
    from bmad_orchestrator.agent.tools.operational import set_model

    r = await _call(set_model, {"role": "dev", "model": "claude-sonnet-4-6"})
    p = _payload(r)
    assert Path(p["config_path"]).exists()


@pytest.mark.asyncio
async def test_operational_schedule_reminder_validates_iso() -> None:
    from bmad_orchestrator.agent.tools.operational import schedule_reminder

    r = await _call(
        schedule_reminder,
        {"when_iso": "2026-12-31T10:00:00", "message": "deploy review"},
    )
    p = _payload(r)
    assert p["reminder_id"].startswith("r-")

    r2 = await _call(schedule_reminder, {"when_iso": "bogus", "message": "x"})
    assert _is_error(r2)


@pytest.mark.asyncio
async def test_operational_set_voice_provider_rejects_invalid() -> None:
    from bmad_orchestrator.agent.tools.operational import set_voice_provider

    ok = await _call(set_voice_provider, {"channel": "stt", "provider": "whisper_local"})
    assert not _is_error(ok)
    bad = await _call(set_voice_provider, {"channel": "stt", "provider": "magic"})
    assert _is_error(bad)


# ── Splitter (2) ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_splitter_keeps_small_story() -> None:
    from bmad_orchestrator.agent.tools.splitter import check_should_split

    r = await _call(check_should_split, {"story_id": "1-1-tenant-signup"})
    p = _payload(r)
    assert p["decision"] == "keep"


@pytest.mark.asyncio
async def test_splitter_unknown_story_errors() -> None:
    from bmad_orchestrator.agent.tools.splitter import check_should_split

    r = await _call(check_should_split, {"story_id": "ghost"})
    assert _is_error(r)


@pytest.mark.asyncio
async def test_splitter_split_story_writes_substories() -> None:
    from bmad_orchestrator.agent.tools.splitter import split_story

    r = await _call(
        split_story,
        {
            "story_id": "1-2-tenant-activate",
            "sub_stories": [
                {"id": "1-2a-activate-email"},
                {"id": "1-2b-activate-sms"},
            ],
        },
    )
    p = _payload(r)
    assert p["sub_ids"] == ["1-2a-activate-email", "1-2b-activate-sms"]


# ── Escalate (2) ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_escalate_to_human_emits_event() -> None:
    from bmad_orchestrator.agent.tools.escalate import escalate_to_human

    r = await _call(
        escalate_to_human,
        {"reason": "budget>$50", "context": {"story": "1-1"}},
    )
    p = _payload(r)
    assert p["escalation_id"].startswith("esc-")
    assert Path(p["event_path"]).exists()


@pytest.mark.asyncio
async def test_update_sprint_status_writes_and_changes() -> None:
    from bmad_orchestrator.agent.tools._common import read_sprint_status_yaml
    from bmad_orchestrator.agent.tools.escalate import update_sprint_status

    r = await _call(
        update_sprint_status,
        {"story_id": "1-1-tenant-signup", "status": "in-progress"},
    )
    p = _payload(r)
    assert p["prev_status"] == "ready-for-dev"
    assert p["status"] == "in-progress"

    sprint = read_sprint_status_yaml()
    assert sprint["epics"]["1"]["stories"]["1-1-tenant-signup"] == "in-progress"
