"""PreToolUse / PostToolUse hooks (spec §9 layer 1).

Deny-list:
- rm -rf
- git push --force / -f
- git commit --no-verify
- git reset --hard main / master
- Any tool call against worktree path == project main
"""

from __future__ import annotations

from typing import Any

DANGEROUS_BASH_PATTERNS: tuple[str, ...] = (
    "rm -rf",
    "rm  -rf",
    "rm -fr",
    "git push --force",
    "git push -f",
    "git commit --no-verify",
    "git reset --hard main",
    "git reset --hard master",
    "git reset --hard origin/main",
    "git reset --hard origin/master",
)


async def security_check_hook(
    input_data: dict[str, Any],
    tool_use_id: str | None,
    context: Any,
) -> dict[str, Any]:
    """PreToolUse hook. Returns deny if dangerous combo detected."""
    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {})

    if tool_name == "Bash":
        command: str = tool_input.get("command", "")
        for pattern in DANGEROUS_BASH_PATTERNS:
            if pattern in command:
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": f"Blocked dangerous pattern: {pattern}",
                    }
                }

    # TODO: add branch isolation check for git_merge tool

    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
        }
    }


async def audit_tool_output(
    input_data: dict[str, Any],
    tool_use_id: str | None,
    context: Any,
) -> dict[str, Any]:
    """PostToolUse hook — append to audit log."""
    # TODO: write JSONL audit entry
    return {}
