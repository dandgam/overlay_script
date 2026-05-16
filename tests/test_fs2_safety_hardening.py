"""FS2 acceptance tests — safety hooks hardening + signed-token main-merge +
bot callback whitelist (spec §B2, §B3, §B4, §C5, §M3, §M8).

Each test corresponds to a PoC bypass listed in the spec acceptance section.
All bypass attempts MUST → deny / refuse / dropped.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from bmad_orchestrator.agent.safety import (
    generate_token,
    has_active_token,
    revoke_token,
    security_check_hook,
    validate_token,
)
from bmad_orchestrator.agent.safety.main_merge_token import (
    consume_token,
    token_path,
)
from bmad_orchestrator.agent.tools.merge import git_merge as _git_merge_tool
from bmad_orchestrator.bot import handlers

# Unwrap the SDK tool decorator for direct invocation in tests.
git_merge = _git_merge_tool.handler


# ── fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = Path(__file__).parent / "fixtures" / "mock-odyssey"
    dst = tmp_path / "mock-odyssey"
    shutil.copytree(src, dst)
    monkeypatch.setenv("ORCHESTRATOR_TARGET_PROJECT", str(dst))
    monkeypatch.setenv("ORCHESTRATOR_ORCHESTRATOR_HOME", str(tmp_path / "orch"))
    monkeypatch.setenv("ORCHESTRATOR_STATE_DB", str(tmp_path / "state.db"))
    monkeypatch.setenv("BMAD_AUDIT_LOG", str(tmp_path / "audit.events.jsonl"))
    monkeypatch.setenv(
        "BMAD_MAIN_MERGE_TOKEN_PATH", str(tmp_path / "main-merge-token.json")
    )
    monkeypatch.delenv("BMAD_ALLOW_MAIN_MERGE", raising=False)
    monkeypatch.delenv("BMAD_WORKER_WORKTREE", raising=False)


async def _hook(tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    return await security_check_hook(
        {"tool_name": tool_name, "tool_input": tool_input}, "tu-fs2", None
    )


def _decision(reply: dict[str, Any]) -> str:
    return str(reply["hookSpecificOutput"]["permissionDecision"])


def _reason(reply: dict[str, Any]) -> str:
    return str(reply["hookSpecificOutput"].get("permissionDecisionReason", ""))


# ── B3 — bash shlex tokenize + canonical flag parser ──────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command,expected_pattern",
    [
        # Whitespace-collapse bypass family (C5): shlex collapses any run of WS
        ("rm  -rf /tmp/x", "rm_recursive_force"),
        ("rm   -rf  /tmp/x", "rm_recursive_force"),
        ("rm\t-rf\t/tmp/x", "rm_recursive_force"),
        # Long-form flags (was missing from substring matcher)
        ("rm --recursive --force /tmp/x", "rm_recursive_force"),
        ("rm --recursive=true --force /tmp/x", "rm_recursive_force"),
        # Combined short flags in any order (mixed case)
        ("rm -fR /tmp/x", "rm_recursive_force"),
        ("rm -Rf /tmp/x", "rm_recursive_force"),
        ("rm -fr /tmp/x", "rm_recursive_force"),
        # sudo wrapper unwrapping
        ("sudo rm -rf /var/log/old", "rm_recursive_force"),
        ("sudo -u root rm -rf /etc", "rm_recursive_force"),
        ("sudo -E rm --recursive --force /tmp/x", "rm_recursive_force"),
    ],
)
async def test_rm_recursive_force_bypass_family(
    command: str, expected_pattern: str
) -> None:
    reply = await _hook("Bash", {"command": command})
    assert _decision(reply) == "deny", f"unexpected allow for: {command!r}"
    assert expected_pattern in _reason(reply), (
        f"reason {_reason(reply)!r} missing {expected_pattern!r} for: {command!r}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    [
        "git push -f origin feature",
        "git push --force origin main",
        "git push --force-with-lease origin main",
        "git push -fu origin feature",
        "git push -uf origin feature",
        "git push origin +main",
        "git push origin +feature:main",
        # Global-flag bypass: `git -c key=value push --force`
        "git -c protocol.version=2 push --force origin main",
        "git -c http.sslVerify=false -C /tmp push -f origin main",
    ],
)
async def test_git_push_force_bypass_family(command: str) -> None:
    reply = await _hook("Bash", {"command": command})
    assert _decision(reply) == "deny", f"unexpected allow for: {command!r}"
    assert "force" in _reason(reply).lower() or "push" in _reason(reply).lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    [
        # Pushing to protected ref via refspec destination
        "git push origin HEAD:main",
        "git push origin feature:main",
        "git push origin HEAD:master",
        "git push origin HEAD:refs/heads/main",
        "git push origin HEAD:refs/heads/master",
    ],
)
async def test_git_push_protected_dest_refspec(command: str) -> None:
    reply = await _hook("Bash", {"command": command})
    assert _decision(reply) == "deny", f"unexpected allow for: {command!r}"
    assert "protected" in _reason(reply).lower() or "main" in _reason(reply).lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    [
        "git commit --no-verify -m 'oops'",
        "git commit -n -m 'oops'",
        "git commit -nm 'oops'",  # combined short -nm (no-verify + message)
        "git commit -mn 'oops'",  # reverse combined order
        # global-flag bypass
        "git -c user.name=foo commit --no-verify -m oops",
    ],
)
async def test_git_commit_no_verify_flag_forms(command: str) -> None:
    reply = await _hook("Bash", {"command": command})
    assert _decision(reply) == "deny", f"unexpected allow for: {command!r}"
    assert "verify" in _reason(reply).lower() or "hook" in _reason(reply).lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    [
        "GIT_COMMIT_NO_VERIFY=1 git commit -m msg",
        "env GIT_COMMIT_NO_VERIFY=1 git commit -m msg",
        "env GIT_NO_VERIFY=true git commit -m msg",
        "GIT_HOOKS_NO_VERIFY=yes git commit -m msg",
    ],
)
async def test_git_commit_no_verify_env_prefix(command: str) -> None:
    reply = await _hook("Bash", {"command": command})
    assert _decision(reply) == "deny", f"unexpected allow for: {command!r}"


# ── B3 — subshell / command-substitution literals ────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    [
        "foo $(rm -rf x)",
        'echo "$(rm -rf /)"',  # quoted in shell BUT still executed
        "echo `rm -rf x`",
        "echo ${IFS}",  # parameter expansion vector
        "bash -c 'rm -rf /'",
        "sh -c 'echo hi'",
        "zsh -c 'echo hi'",
        "eval 'rm -rf /'",
        "exec rm -rf /",
        "source ./malicious.sh",
        # Process substitution
        "diff <(echo foo) <(echo bar)",
    ],
)
async def test_subshell_vectors_denied(command: str) -> None:
    reply = await _hook("Bash", {"command": command})
    assert _decision(reply) == "deny", f"unexpected allow for: {command!r}"
    assert "subshell" in _reason(reply).lower() or "substitution" in _reason(reply).lower()


# ── B3 — sub-command splitting ────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    [
        "echo a; rm -r --force /tmp/x",
        "echo a && rm -rf /tmp/x",
        "echo a || rm -rf /tmp/x",
        "cd /tmp && rm -rf .",
        "ls -la; echo hi; rm -rf /var",
    ],
)
async def test_compound_command_split_catches_evil_second_half(command: str) -> None:
    reply = await _hook("Bash", {"command": command})
    assert _decision(reply) == "deny", f"unexpected allow for: {command!r}"


# ── B3 — benign commands still allowed ────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    [
        "ls -la",
        "git status",
        "git log --oneline -5",
        "pytest tests/",
        "cargo check",
        "rm -i /tmp/foo",  # interactive — allowed (single file removal)
        "rm /tmp/foo",  # no -r/-f — single file
        "git push origin feature",  # no force
        "git push origin HEAD",  # refspec doesn't target main
        "git commit -m 'normal commit'",
        "echo a; echo b",  # both halves benign
        "rm -rf",  # incomplete — but still matches the recursive_force rule (defensive)
    ],
)
async def test_benign_commands_allowed(command: str) -> None:
    reply = await _hook("Bash", {"command": command})
    # `rm -rf` (no target) still denies — intentional defensive policy
    if command == "rm -rf":
        assert _decision(reply) == "deny"
    else:
        assert _decision(reply) == "allow", (
            f"unexpected deny for benign: {command!r}: {_reason(reply)}"
        )


# ── B2 — filesystem write with cwd hint ───────────────────────────────────


@pytest.mark.asyncio
async def test_relative_path_without_cwd_hint_denied() -> None:
    reply = await _hook("Edit", {"file_path": "../../../etc/passwd"})
    assert _decision(reply) == "deny"
    assert "cwd_unknown" in _reason(reply)


@pytest.mark.asyncio
async def test_relative_path_with_cwd_resolved_against_target(tmp_path: Path) -> None:
    # cwd inside target_project → relative path resolves inside roots → allow
    import os

    target_root = Path(os.environ["ORCHESTRATOR_TARGET_PROJECT"])
    reply = await _hook(
        "Edit",
        {"file_path": "src/foo.py", "cwd": str(target_root)},
    )
    assert _decision(reply) == "allow", _reason(reply)


@pytest.mark.asyncio
async def test_relative_path_traversal_escape_denied() -> None:
    import os

    target_root = Path(os.environ["ORCHESTRATOR_TARGET_PROJECT"])
    reply = await _hook(
        "Edit",
        {"file_path": "../../../etc/passwd", "cwd": str(target_root)},
    )
    assert _decision(reply) == "deny"
    assert (
        "out_of_scope" in _reason(reply)
        or "escapes_worktree" in _reason(reply)
        or "outside" in _reason(reply).lower()
    )


@pytest.mark.asyncio
async def test_worker_worktree_env_hint_used(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    target_root = Path(os.environ["ORCHESTRATOR_TARGET_PROJECT"])
    monkeypatch.setenv("BMAD_WORKER_WORKTREE", str(target_root))
    reply = await _hook("Edit", {"file_path": "src/bar.py"})
    assert _decision(reply) == "allow", _reason(reply)


@pytest.mark.asyncio
async def test_worker_worktree_escape_denied(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import os

    target_root = Path(os.environ["ORCHESTRATOR_TARGET_PROJECT"])
    monkeypatch.setenv("BMAD_WORKER_WORKTREE", str(target_root))
    # Absolute path outside the worktree → escapes_worktree rule fires
    outside = str(tmp_path / "outside.txt")
    reply = await _hook("Edit", {"file_path": outside})
    assert _decision(reply) == "deny"


# ── B4 + M3 — signed-token main-merge gate ───────────────────────────────


@pytest.mark.asyncio
async def test_git_checkout_main_denied_without_token() -> None:
    reply = await _hook("Bash", {"command": "git checkout main"})
    assert _decision(reply) == "deny"
    assert "token" in _reason(reply).lower() or "main" in _reason(reply).lower()


@pytest.mark.asyncio
async def test_git_checkout_main_allowed_with_token() -> None:
    generate_token(ttl_seconds=300)
    try:
        reply = await _hook("Bash", {"command": "git checkout main"})
        assert _decision(reply) == "allow", _reason(reply)
        reply = await _hook("Bash", {"command": "git merge integration/foo main"})
        assert _decision(reply) == "allow", _reason(reply)
    finally:
        revoke_token()


@pytest.mark.asyncio
async def test_token_expires() -> None:
    generate_token(ttl_seconds=1)
    try:
        # Manually flip the file's expires_at to the past instead of sleeping
        path = token_path()
        state = json.loads(path.read_text(encoding="utf-8"))
        state["expires_at"] = int(time.time()) - 1
        path.write_text(json.dumps(state), encoding="utf-8")
        ok, reason = has_active_token()
        assert not ok and reason == "token_expired"
        reply = await _hook("Bash", {"command": "git checkout main"})
        assert _decision(reply) == "deny"
    finally:
        revoke_token()


def test_token_consume_is_single_use() -> None:
    token = generate_token(ttl_seconds=300)
    try:
        ok, _ = consume_token(token)
        assert ok
        ok2, reason2 = consume_token(token)
        assert not ok2 and reason2 == "token_used"
        ok3, reason3 = validate_token(token)
        assert not ok3 and reason3 == "token_used"
    finally:
        revoke_token()


def test_token_mismatch_refused() -> None:
    generate_token(ttl_seconds=300)
    try:
        ok, reason = consume_token("not-the-token")
        assert not ok and reason == "token_mismatch"
    finally:
        revoke_token()


def test_token_missing_returns_no_token() -> None:
    ok, reason = consume_token("anything")
    assert not ok and reason == "no_token"


def test_token_file_is_chmod_600() -> None:
    generate_token(ttl_seconds=300)
    try:
        import stat

        mode = stat.S_IMODE(token_path().stat().st_mode)
        assert mode == 0o600
    finally:
        revoke_token()


# ── B4 — git_merge tool refuses target=main without token ─────────────────


def _run(coro: Any) -> Any:
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.mark.asyncio
async def test_git_merge_tool_refuses_main_without_token(tmp_path: Path) -> None:
    # Pretend worktree exists but no .git — exercise the gate before subprocess
    worktree = tmp_path / "fake-worktree"
    worktree.mkdir()
    result = await git_merge(
        {"worktree": str(worktree), "target_branch": "main", "message": "x"}
    )
    body = json.loads(result["content"][0]["text"])
    assert result.get("isError") is True
    assert body["error"] == "main_merge_token_required"


@pytest.mark.asyncio
async def test_git_merge_tool_refuses_main_with_bad_token(tmp_path: Path) -> None:
    worktree = tmp_path / "fake-worktree"
    worktree.mkdir()
    generate_token(ttl_seconds=300)
    try:
        result = await git_merge(
            {
                "worktree": str(worktree),
                "target_branch": "main",
                "message": "x",
                "signed_token": "wrong-token",
            }
        )
        body = json.loads(result["content"][0]["text"])
        assert result.get("isError") is True
        assert body["error"] == "main_merge_token_required"
    finally:
        revoke_token()


@pytest.mark.asyncio
async def test_git_merge_tool_consumes_token_on_main_target(tmp_path: Path) -> None:
    worktree = tmp_path / "fake-worktree"
    worktree.mkdir()
    token = generate_token(ttl_seconds=300)
    try:
        result = await git_merge(
            {
                "worktree": str(worktree),
                "target_branch": "main",
                "message": "x",
                "signed_token": token,
            }
        )
        # Mock-mode merge succeeds; token must now be consumed
        body = json.loads(result["content"][0]["text"])
        assert body["merged"] is True
        assert body["mock"] is True
        ok, reason = validate_token(token)
        assert not ok and reason == "token_used"
    finally:
        revoke_token()


@pytest.mark.asyncio
async def test_git_merge_tool_allows_non_main_target(tmp_path: Path) -> None:
    worktree = tmp_path / "fake-worktree"
    worktree.mkdir()
    result = await git_merge(
        {
            "worktree": str(worktree),
            "target_branch": "integration/whatever",
            "message": "x",
        }
    )
    body = json.loads(result["content"][0]["text"])
    assert body["merged"] is True
    assert body["target_branch"] == "integration/whatever"


# ── M8 — bot callback whitelist ──────────────────────────────────────────


def _mk_callback_update(chat_id: int, data: str) -> MagicMock:
    update = MagicMock()
    update.effective_chat.id = chat_id
    update.message = None
    update.callback_query = MagicMock()
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    update.callback_query.data = data
    return update


def _install_handlers_settings(monkeypatch: pytest.MonkeyPatch, chat_id: int) -> None:
    from bmad_orchestrator.config import Settings, TelegramConfig

    s = Settings()
    s.telegram = TelegramConfig(bot_token="x", chat_id_whitelist=[chat_id])
    monkeypatch.setattr("bmad_orchestrator.bot.handlers.load_settings", lambda: s)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data",
    ["evil:payload", "exec:rm -rf /", "raw_string", "../prefix:bad", "drop:table"],
)
async def test_callback_unknown_prefix_dropped(
    monkeypatch: pytest.MonkeyPatch, data: str
) -> None:
    _install_handlers_settings(monkeypatch, 42)
    forward_mock = AsyncMock(return_value="should-not-be-called")
    monkeypatch.setattr(handlers, "forward_to_agent", forward_mock)
    update = _mk_callback_update(42, data)
    await handlers.callback(update, MagicMock())
    forward_mock.assert_not_awaited()
    update.callback_query.edit_message_text.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data",
    ["stop:graceful", "stop:hard", "stop:cancel"],
)
async def test_callback_stop_prefix_allowed(
    monkeypatch: pytest.MonkeyPatch, data: str
) -> None:
    _install_handlers_settings(monkeypatch, 42)
    forward_mock = AsyncMock(return_value="ack")
    monkeypatch.setattr(handlers, "forward_to_agent", forward_mock)
    update = _mk_callback_update(42, data)
    await handlers.callback(update, MagicMock())
    update.callback_query.edit_message_text.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data",
    ["proposal:accept-A1", "merge:integration/foo", "confirm:yes", "cancel:abort-1"],
)
async def test_callback_other_whitelisted_prefixes_forwarded(
    monkeypatch: pytest.MonkeyPatch, data: str
) -> None:
    _install_handlers_settings(monkeypatch, 42)
    forward_mock = AsyncMock(return_value="ack")
    monkeypatch.setattr(handlers, "forward_to_agent", forward_mock)
    update = _mk_callback_update(42, data)
    await handlers.callback(update, MagicMock())
    forward_mock.assert_awaited_once()
    update.callback_query.edit_message_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_callback_unknown_prefix_audited(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_handlers_settings(monkeypatch, 42)
    monkeypatch.setenv("BMAD_TELEGRAM_AUDIT_LOG", str(tmp_path / "tg.jsonl"))
    monkeypatch.setattr(handlers, "forward_to_agent", AsyncMock())
    update = _mk_callback_update(42, "evil:do-bad-thing")
    await handlers.callback(update, MagicMock())
    log = (tmp_path / "tg.jsonl").read_text(encoding="utf-8").splitlines()
    parsed = [json.loads(line) for line in log if line]
    drops = [p for p in parsed if p.get("extra", {}).get("action") == "dropped"]
    assert drops, "expected an audit entry for the dropped callback"
    assert drops[0]["extra"]["reason"] == "unknown_prefix"
