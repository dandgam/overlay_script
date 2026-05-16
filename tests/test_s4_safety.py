"""S4 acceptance tests — Safety 3-layer (spec §9).

Coverage:
- Layer 1 PreToolUse: deny rm -rf, git push --force, git commit --no-verify,
  git reset --hard main; deny path-escape Edit/Write; allow benign commands.
- Layer 2 budget_guard: story alarm/halt thresholds; batch alarm/halt; halt event
  emitted into EventLoop; mock workflow halts on hard cap.
- Layer 3 branch_isolation: validate_merge_target denies direct main merge,
  validate_worker_write_path blocks escape from worktree.
- audit_event tool: appends JSONL entry to audit_log_path.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from bmad_orchestrator.agent.safety import (
    BudgetGuard,
    audit_log_path,
    generate_token,
    record_audit,
    revoke_token,
    security_check_hook,
    validate_merge_target,
    validate_worker_write_path,
)
from bmad_orchestrator.config import BudgetConfig
from bmad_orchestrator.runtime.event_loop import EventLoop, EventType


@pytest.fixture(autouse=True)
def _isolate_target_project(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = Path(__file__).parent / "fixtures" / "mock-odyssey"
    dst = tmp_path / "mock-odyssey"
    shutil.copytree(src, dst)
    monkeypatch.setenv("ORCHESTRATOR_TARGET_PROJECT", str(dst))
    monkeypatch.setenv("ORCHESTRATOR_STATE_DB", str(tmp_path / "state.db"))
    monkeypatch.setenv("ORCHESTRATOR_ORCHESTRATOR_HOME", str(tmp_path / "orch"))
    monkeypatch.setenv("BMAD_AUDIT_LOG", str(tmp_path / "audit.events.jsonl"))
    monkeypatch.setenv(
        "BMAD_MAIN_MERGE_TOKEN_PATH", str(tmp_path / "main-merge-token.json")
    )
    monkeypatch.delenv("BMAD_ALLOW_MAIN_MERGE", raising=False)
    monkeypatch.delenv("BMAD_WORKER_WORKTREE", raising=False)


# ── helpers ────────────────────────────────────────────────────────────────────


async def _hook(tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    return await security_check_hook(
        {"tool_name": tool_name, "tool_input": tool_input}, "tu-1", None
    )


def _decision(reply: dict[str, Any]) -> str:
    return str(reply["hookSpecificOutput"]["permissionDecision"])


def _reason(reply: dict[str, Any]) -> str:
    return str(reply["hookSpecificOutput"].get("permissionDecisionReason", ""))


def _read_audit(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


# ── Layer 1 — PreToolUse deny rules ────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    [
        "rm -rf /tmp/foo",
        "rm -rf ~",
        "rm -fr /home/server/some/path",
        "sudo rm -rf /var/log/old",
        "cd /tmp && rm -rf .",
    ],
)
async def test_pretooluse_denies_rm_rf(command: str) -> None:
    reply = await _hook("Bash", {"command": command})
    assert _decision(reply) == "deny"
    assert "rm" in _reason(reply).lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    [
        "git push --force origin main",
        "git push -f origin feature",
        "git push --force-with-lease origin main",
    ],
)
async def test_pretooluse_denies_git_push_force(command: str) -> None:
    reply = await _hook("Bash", {"command": command})
    assert _decision(reply) == "deny"
    assert "force" in _reason(reply).lower() or "push" in _reason(reply).lower()


@pytest.mark.asyncio
async def test_pretooluse_denies_git_commit_no_verify() -> None:
    reply = await _hook("Bash", {"command": "git commit --no-verify -m 'oops'"})
    assert _decision(reply) == "deny"
    assert "verify" in _reason(reply).lower() or "hook" in _reason(reply).lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    [
        "git reset --hard main",
        "git reset --hard master",
        "git reset --hard origin/main",
        "git reset --hard origin/master",
        "git reset --hard",
    ],
)
async def test_pretooluse_denies_git_reset_hard_main(command: str) -> None:
    reply = await _hook("Bash", {"command": command})
    assert _decision(reply) == "deny"


@pytest.mark.asyncio
async def test_pretooluse_denies_git_main_merge_without_flag() -> None:
    reply = await _hook("Bash", {"command": "git checkout main"})
    assert _decision(reply) == "deny"
    reply = await _hook("Bash", {"command": "git merge integration/foo main"})
    assert _decision(reply) == "deny"


@pytest.mark.asyncio
async def test_pretooluse_allows_main_merge_with_signed_token() -> None:
    generate_token(ttl_seconds=300)
    try:
        reply = await _hook("Bash", {"command": "git checkout main"})
        assert _decision(reply) == "allow"
    finally:
        revoke_token()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    [
        "ls -la",
        "git status",
        "git log --oneline -5",
        "pytest tests/",
        "cargo check",
        "echo hello",
        "",
    ],
)
async def test_pretooluse_allows_safe_commands(command: str) -> None:
    reply = await _hook("Bash", {"command": command})
    assert _decision(reply) == "allow"


@pytest.mark.asyncio
async def test_pretooluse_denies_filesystem_escape(tmp_path: Path) -> None:
    # outside both target_project and orchestrator_home
    outside = "/etc/passwd"
    reply = await _hook("Edit", {"file_path": outside})
    assert _decision(reply) == "deny"
    assert "out_of_scope" in _reason(reply) or "outside" in _reason(reply).lower()


@pytest.mark.asyncio
async def test_pretooluse_allows_filesystem_within_target_project() -> None:
    target = os.environ["ORCHESTRATOR_TARGET_PROJECT"]
    candidate = str(Path(target) / "src" / "tenant" / "signup.py")
    reply = await _hook("Edit", {"file_path": candidate})
    assert _decision(reply) == "allow"


@pytest.mark.asyncio
async def test_pretooluse_audit_logs_deny() -> None:
    await _hook("Bash", {"command": "rm -rf /tmp/x"})
    entries = _read_audit(audit_log_path())
    assert any(e["event_type"] == "pretooluse_deny" for e in entries)
    deny = next(e for e in entries if e["event_type"] == "pretooluse_deny")
    assert deny["tool_name"] == "Bash"
    assert deny["pattern"] == "rm_recursive_force"


# ── Layer 2 — Budget guard ─────────────────────────────────────────────────────


def test_budget_story_thresholds_default() -> None:
    cfg = BudgetConfig()
    guard = BudgetGuard(cfg)
    assert guard.check_story(10.0).level == "ok"
    assert guard.check_story(29.99).level == "ok"
    assert guard.check_story(30.0).level == "alarm"
    assert guard.check_story(49.99).level == "alarm"
    assert guard.check_story(50.0).level == "halt"
    assert guard.check_story(75.0).level == "halt"


def test_budget_batch_thresholds_default() -> None:
    cfg = BudgetConfig()
    guard = BudgetGuard(cfg)
    assert guard.check_batch(150.0).level == "ok"
    assert guard.check_batch(200.0).level == "alarm"
    assert guard.check_batch(299.0).level == "alarm"
    assert guard.check_batch(300.0).level == "halt"
    assert guard.check_batch(450.0).level == "halt"


@pytest.mark.asyncio
async def test_budget_halt_emits_event() -> None:
    cfg = BudgetConfig()
    loop = EventLoop()
    guard = BudgetGuard(cfg, event_loop=loop)
    result = await guard.enforce_story(spent_usd=55.0, story_id="1-1-tenant-signup")
    assert result.level == "halt"
    event = await asyncio.wait_for(loop.next(), timeout=1.0)
    assert event is not None
    assert event.type == EventType.BUDGET_THRESHOLD_HIT
    assert event.payload["scope"] == "story"
    assert event.payload["level"] == "halt"
    assert event.payload["story_id"] == "1-1-tenant-signup"


@pytest.mark.asyncio
async def test_budget_alarm_emits_event_but_continues() -> None:
    cfg = BudgetConfig()
    loop = EventLoop()
    guard = BudgetGuard(cfg, event_loop=loop)
    result = await guard.enforce_batch(spent_usd=220.0, wave="1a")
    assert result.level == "alarm"
    event = await asyncio.wait_for(loop.next(), timeout=1.0)
    assert event.payload["level"] == "alarm"
    assert event.payload["scope"] == "batch"


@pytest.mark.asyncio
async def test_budget_ok_does_not_emit() -> None:
    cfg = BudgetConfig()
    loop = EventLoop()
    guard = BudgetGuard(cfg, event_loop=loop)
    result = await guard.enforce_story(spent_usd=5.0, story_id="x")
    assert result.level == "ok"
    nothing = await loop.next(timeout=0.2)
    assert nothing is None


@pytest.mark.asyncio
async def test_budget_halt_writes_audit() -> None:
    cfg = BudgetConfig()
    guard = BudgetGuard(cfg)
    await guard.enforce_story(spent_usd=99.0, story_id="z")
    entries = _read_audit(audit_log_path())
    assert any(
        e["event_type"] == "budget_threshold_hit" and e.get("level") == "halt"
        for e in entries
    )


@pytest.mark.asyncio
async def test_budget_halts_mock_workflow() -> None:
    """End-to-end: budget guard subscriber on EventLoop stops the loop on halt."""
    cfg = BudgetConfig()
    loop = EventLoop()
    guard = BudgetGuard(cfg, event_loop=loop)
    halted = asyncio.Event()

    async def halt_subscriber(event: Any) -> None:
        if event.type == EventType.BUDGET_THRESHOLD_HIT and event.payload.get("level") == "halt":
            halted.set()

    loop.on(halt_subscriber)

    # Спендим деньги — каждое story прибавляет $20, к 3-му story тригер.
    spent = 0.0
    for sid in ("a", "b", "c"):
        spent += 20.0
        await guard.enforce_story(spent_usd=spent, story_id=sid)
        await loop.dispatch_one(timeout=0.5)
        if halted.is_set():
            break

    assert halted.is_set(), "mock workflow did not halt at $50 cap"


# ── Layer 3 — Branch isolation ─────────────────────────────────────────────────


def test_validate_merge_target_denies_main() -> None:
    ok, reason = validate_merge_target("main", has_human_approval=False)
    assert ok is False
    assert "main" in reason


def test_validate_merge_target_allows_main_with_approval() -> None:
    ok, _ = validate_merge_target("main", has_human_approval=True)
    assert ok is True


def test_validate_merge_target_allows_feature_branch() -> None:
    ok, _ = validate_merge_target("feature/story-1-1", has_human_approval=False)
    assert ok is True


def test_validate_worker_write_path_allows_inside(tmp_path: Path) -> None:
    worktree = tmp_path / "wt1"
    worktree.mkdir()
    target = worktree / "src" / "foo.py"
    target.parent.mkdir(parents=True)
    target.write_text("# stub")
    ok, _ = validate_worker_write_path(target, worktree)
    assert ok is True


def test_validate_worker_write_path_denies_escape(tmp_path: Path) -> None:
    worktree = tmp_path / "wt1"
    worktree.mkdir()
    sibling = tmp_path / "elsewhere" / "evil.py"
    sibling.parent.mkdir(parents=True)
    sibling.write_text("# attacker")
    ok, reason = validate_worker_write_path(sibling, worktree)
    assert ok is False
    assert "escape" in reason or "outside" in reason.lower()


def test_validate_worker_write_path_denies_parent_traversal(tmp_path: Path) -> None:
    worktree = tmp_path / "wt1"
    worktree.mkdir()
    traversal = worktree / ".." / "secrets.txt"
    ok, _ = validate_worker_write_path(traversal, worktree)
    assert ok is False


# ── audit_event tool wiring ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_audit_event_tool_appends_jsonl() -> None:
    from bmad_orchestrator.agent.tools import ALL_TOOLS, tool_names
    from bmad_orchestrator.agent.tools.audit import audit_event

    assert "audit_event" in tool_names()
    assert any(t.name == "audit_event" for t in ALL_TOOLS)

    reply = await audit_event.handler(
        {
            "event_type": "decision_log",
            "summary": "promoted S4 manually",
            "payload": {"by": "user", "ref": "S4-promote"},
        }
    )
    assert "isError" not in reply or reply.get("isError") in (False, None)
    body = json.loads(reply["content"][0]["text"])
    recorded = body["recorded"]
    assert recorded["event_type"] == "decision_log"
    assert recorded["summary"] == "promoted S4 manually"
    assert recorded["by"] == "user"

    entries = _read_audit(audit_log_path())
    assert any(e["event_type"] == "decision_log" for e in entries)


@pytest.mark.asyncio
async def test_audit_event_tool_rejects_empty_type() -> None:
    from bmad_orchestrator.agent.tools.audit import audit_event

    reply = await audit_event.handler({"event_type": "", "payload": {}})
    assert reply.get("isError") is True


def test_record_audit_writes_jsonl_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "custom-audit.jsonl"
    monkeypatch.setenv("BMAD_AUDIT_LOG", str(target))
    record_audit("first", a=1)
    record_audit("second", b="x")
    lines = target.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    parsed = [json.loads(line) for line in lines]
    assert parsed[0]["event_type"] == "first"
    assert parsed[1]["b"] == "x"
