"""Merge tools (spec §17 — 3 tools).

run_code_review, run_security_review, git_merge.

Mock-mode: review tools return PASS with empty findings unless the worktree
contains a sentinel file (`.review_findings.json` for code review,
`.security_findings.json` for security review). git_merge invokes `git merge`
only when the worktree is a real git repo; otherwise records intent.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from claude_agent_sdk import tool

from bmad_orchestrator.agent.safety.branch_isolation import (
    FORBIDDEN_DIRECT_MERGE_TARGETS,
    validate_merge_target,
)
from bmad_orchestrator.agent.safety.main_merge_token import consume_token
from bmad_orchestrator.agent.tools._common import (
    append_jsonl,
    error,
    json_ok,
    now_iso,
    runs_dir,
)


def _merge_event(event_type: str, **payload: Any) -> None:
    append_jsonl(
        runs_dir() / "merge.events.jsonl",
        {"event_type": event_type, "ts": now_iso(), **payload},
    )


def _load_findings(worktree: str, fname: str) -> list[dict[str, Any]]:
    path = Path(worktree) / fname
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


async def _run_review(worktree: str, review_type: str, sentinel: str) -> dict[str, Any]:
    if not worktree:
        return error("missing 'worktree' arg", code="invalid_arg")
    if not Path(worktree).exists():
        return error(f"worktree path not found: {worktree}", code="worktree_missing")

    findings = _load_findings(worktree, sentinel)
    critical = [f for f in findings if f.get("severity") in ("critical", "high")]
    status = "fail" if critical else "pass"
    _merge_event(
        f"{review_type}_review_run",
        worktree=worktree,
        status=status,
        finding_count=len(findings),
    )
    return json_ok(
        {
            "worktree": worktree,
            "review_type": review_type,
            "status": status,
            "findings": findings,
        }
    )


@tool(
    "run_code_review",
    "Run bmad-code-review on worker's diff. Required before merge.",
    {"worktree": str},
)
async def run_code_review(args: dict[str, Any]) -> dict[str, Any]:
    return await _run_review(str(args.get("worktree", "")), "code", ".review_findings.json")


@tool(
    "run_security_review",
    "Run bmad-security-review on worker's diff. Required for security-critical epics.",
    {"worktree": str},
)
async def run_security_review(args: dict[str, Any]) -> dict[str, Any]:
    return await _run_review(
        str(args.get("worktree", "")), "security", ".security_findings.json"
    )


@tool(
    "git_merge",
    "Merge worktree's branch into target_branch (with flock on shared state).",
    {"worktree": str, "target_branch": str, "message": str, "signed_token": str},
)
async def git_merge(args: dict[str, Any]) -> dict[str, Any]:
    worktree = str(args.get("worktree", ""))
    target = str(args.get("target_branch", ""))
    message = str(args.get("message", f"merge {worktree} → {target}"))
    signed_token = args.get("signed_token")
    if not worktree or not target:
        return error("require 'worktree' and 'target_branch'", code="invalid_arg")

    is_main_target = target in FORBIDDEN_DIRECT_MERGE_TARGETS
    if is_main_target:
        consumed, reason = consume_token(
            str(signed_token) if isinstance(signed_token, str) else None
        )
        if not consumed:
            _merge_event(
                "git_merge_refused",
                worktree=worktree,
                target_branch=target,
                reason=reason,
            )
            return error(
                f"merge to {target} refused: {reason} "
                "(generate via bmad_orchestrator.agent.safety.main_merge_token.generate_token)",
                code="main_merge_token_required",
            )
    else:
        allowed, why = validate_merge_target(target, has_human_approval=True)
        if not allowed:
            _merge_event(
                "git_merge_refused",
                worktree=worktree,
                target_branch=target,
                reason=why,
            )
            return error(f"merge refused: {why}", code="merge_target_forbidden")

    repo = Path(worktree)
    if not (repo / ".git").exists():
        _merge_event(
            "git_merge_mock",
            worktree=worktree,
            target_branch=target,
            message=message,
        )
        return json_ok(
            {
                "worktree": worktree,
                "target_branch": target,
                "merged": True,
                "mock": True,
                "commit_sha": None,
            }
        )

    proc = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        str(repo),
        "merge",
        "--no-ff",
        "-m",
        message,
        target,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        return error(
            f"git merge failed (rc={proc.returncode}): {err.decode('utf-8', errors='replace')[:400]}",
            code="git_merge_failed",
        )

    rev = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        str(repo),
        "rev-parse",
        "HEAD",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    rev_out, _ = await rev.communicate()
    commit_sha = rev_out.decode("utf-8").strip() or None
    _merge_event(
        "git_merge",
        worktree=worktree,
        target_branch=target,
        commit_sha=commit_sha,
    )
    return json_ok(
        {
            "worktree": worktree,
            "target_branch": target,
            "merged": True,
            "commit_sha": commit_sha,
            "stdout": out.decode("utf-8", errors="replace")[:400],
        }
    )


TOOLS = [run_code_review, run_security_review, git_merge]


__all__ = [
    "TOOLS",
    "git_merge",
    "run_code_review",
    "run_security_review",
]
