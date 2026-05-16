"""PreToolUse / PostToolUse hooks (spec §9 layer 1).

Deny-list rules:
- `rm -rf`, `rm -fr`, любая форма с глобом /, ~, *
- `git push --force` / `-f`
- `git commit --no-verify`
- `git reset --hard <main|master|origin/main|origin/master>` или без аргументов
- Edit/Write на пути за пределами разрешённой агентской зоны
  (только integration/<slug>, worker worktrees; запрещены backup/* / main checkout root
   и любые пути за пределами `orchestrator_home` / `target_project`)

Все deny события прокачиваются через `record_audit` в JSONL для последующего разбора.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

from bmad_orchestrator.agent.safety.audit import record_audit
from bmad_orchestrator.agent.safety.branch_isolation import validate_worker_write_path
from bmad_orchestrator.agent.tools._common import get_settings

_DANGEROUS_PHRASES: tuple[tuple[str, str], ...] = (
    ("rm -rf", "rm -rf is irreversible"),
    ("rm -fr", "rm -fr is irreversible"),
    ("rm  -rf", "rm -rf is irreversible"),
    ("git push --force", "force push can overwrite remote work"),
    ("git push -f ", "force push can overwrite remote work"),
    ("git push --force-with-lease", "force-with-lease is still destructive"),
    ("git commit --no-verify", "bypassing hooks defeats pre-commit gates"),
    ("git commit -n ", "bypassing hooks defeats pre-commit gates"),
    ("git clean -f", "git clean -f is irreversible"),
    ("git clean -fd", "git clean -fd is irreversible"),
    ("git clean -df", "git clean -fd is irreversible"),
)

_GIT_RESET_HARD_RE = re.compile(
    r"git\s+reset\s+--hard(?:\s+(main|master|origin/main|origin/master))?(?:\s|$)"
)

_WHITESPACE_RM_RF_RE = re.compile(r"\brm\s+(?:-[a-zA-Z]+\s+)*-(?:rf|fr|r\s+-f|f\s+-r)\b")


def _scan_bash(command: str) -> tuple[bool, str | None, str | None]:
    """Return (denied, pattern_id, reason). Empty command always allowed."""
    if not command:
        return False, None, None

    stripped = command.strip()

    # rm -rf на абсолютных / glob путях — отдельный детектор.
    if _WHITESPACE_RM_RF_RE.search(stripped):
        # Любая форма `rm -rf <path>` блокируется, даже если path относительный —
        # spec §9.1 формулирует это как абсолютный bann.
        return True, "rm_rf", "rm -rf is irreversible"

    for phrase, reason in _DANGEROUS_PHRASES:
        if phrase in stripped:
            return True, phrase.strip(), reason

    if _GIT_RESET_HARD_RE.search(stripped):
        return True, "git_reset_hard_main", "git reset --hard on main/master is irreversible"

    return False, None, None


def _agent_write_roots() -> tuple[Path, ...]:
    """Allowed roots for Edit/Write. Symlinks resolved."""
    settings = get_settings()
    roots: list[Path] = []
    for p in (settings.target_project, settings.orchestrator_home):
        try:
            roots.append(p.resolve())
        except OSError:
            roots.append(p)
    return tuple(roots)


def _is_under(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def _scan_filesystem_write(tool_name: str, tool_input: dict[str, Any]) -> tuple[bool, str | None, str | None]:
    """Edit/Write/NotebookEdit checks — пути должны лежать внутри agent roots."""
    if tool_name not in {"Edit", "Write", "NotebookEdit", "MultiEdit"}:
        return False, None, None
    path_str = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not isinstance(path_str, str) or not path_str:
        return False, None, None
    candidate = Path(path_str)
    if not candidate.is_absolute():
        return False, None, None  # relative path — позволяем; tools resolve через cwd

    try:
        resolved = candidate.resolve(strict=False)
    except OSError:
        resolved = candidate
    roots = _agent_write_roots()
    if not any(_is_under(resolved, root) for root in roots):
        return True, "fs_write_out_of_scope", (
            f"Edit/Write to {resolved} outside allowed roots {[str(r) for r in roots]}"
        )
    return False, None, None


def _is_git_merge_into_main(command: str) -> bool:
    """`git merge ... main` или `git checkout main && git merge ...` — нельзя без human approval.

    Этот хук защищает от случайного autonomous main merge — он разрешён только
    при явном `Auto merge: true` в трекере (workflow вызывает merge с
    `BMAD_ALLOW_MAIN_MERGE=1`).
    """
    tokens = shlex.split(command, posix=True) if command else []
    if not tokens:
        return False
    # Простая проверка: 'git checkout main' / 'git merge <branch> main'
    if tokens[:2] == ["git", "checkout"] and "main" in tokens[2:]:
        return True
    if tokens[:2] == ["git", "merge"] and "main" in tokens[2:]:
        return True
    return False


async def security_check_hook(
    input_data: dict[str, Any],
    tool_use_id: str | None,
    context: Any,
) -> dict[str, Any]:
    """PreToolUse hook — deny dangerous tool calls per spec §9.1."""
    tool_name = str(input_data.get("tool_name", ""))
    tool_input = input_data.get("tool_input", {}) or {}

    denied = False
    pattern: str | None = None
    reason: str | None = None

    if tool_name == "Bash":
        command = str(tool_input.get("command", ""))
        denied, pattern, reason = _scan_bash(command)
        if not denied and _is_git_merge_into_main(command):
            import os

            if os.environ.get("BMAD_ALLOW_MAIN_MERGE") != "1":
                denied, pattern, reason = (
                    True,
                    "main_merge_without_flag",
                    "main merge requires BMAD_ALLOW_MAIN_MERGE=1",
                )

    if not denied:
        denied, pattern, reason = _scan_filesystem_write(tool_name, tool_input)

    if denied:
        record_audit(
            "pretooluse_deny",
            tool_name=tool_name,
            tool_use_id=tool_use_id,
            pattern=pattern,
            reason=reason,
            tool_input=tool_input,
        )
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"{pattern}: {reason}",
            }
        }

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
    """PostToolUse hook — pipes tool result into the audit log JSONL."""
    tool_name = str(input_data.get("tool_name", ""))
    if not tool_name:
        return {}
    record_audit(
        "posttooluse",
        tool_name=tool_name,
        tool_use_id=tool_use_id,
        tool_input=input_data.get("tool_input"),
        tool_output_present=input_data.get("tool_response") is not None,
    )
    return {}


__all__ = [
    "audit_tool_output",
    "security_check_hook",
    "validate_worker_write_path",  # re-export for convenience
]
