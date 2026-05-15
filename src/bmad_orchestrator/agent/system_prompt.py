"""System prompt builder.

КРИТИЧНО (spec §11.2): cache_control блоки ОБЯЗАТЕЛЬНО с explicit ttl="1h".
С 06.03.2026 Anthropic default = 5min — без explicit cache hit rate = 0.

Структура (spec §17.4):
1. Project context (~25K tokens) — ttl="1h"
2. Operational rules
3. Tool metadata (22 tools, names + 1-line descriptions, не full schemas — full через tool-search-tool beta)
4. Skill metadata (10 skills, ~100 tokens each)
5. Few-shot examples (10-15 пар)
6. Personality
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def build_system_prompt(
    project_root: Path,
    wave: str,
    locale: str = "ru",
) -> list[dict[str, Any]]:
    """Build cached system prompt blocks for ClaudeSDKClient.

    Returns list of TextBlockParam dicts с cache_control где надо.
    """
    blocks: list[dict[str, Any]] = []

    # 1. Project context — heavy, cache 1h
    project_context = _load_project_context(project_root, wave)
    blocks.append(
        {
            "type": "text",
            "text": project_context,
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        }
    )

    # 2. Operational rules — stable per session
    blocks.append(
        {
            "type": "text",
            "text": _operational_rules(locale),
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        }
    )

    # 3. Tool + skill metadata — stable per release
    blocks.append(
        {
            "type": "text",
            "text": _tool_and_skill_metadata(),
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        }
    )

    # 4. Few-shot examples — stable, but small
    blocks.append({"type": "text", "text": _few_shot_examples(locale)})

    # 5. Personality
    blocks.append({"type": "text", "text": _personality(locale)})

    return blocks


def _load_project_context(project_root: Path, wave: str) -> str:
    """Load CLAUDE.md + epics.md + architecture.md + current sprint-status.

    Concat в один большой блок ~25K tokens.
    """
    # TODO: implement actual loading
    return f"# Project context placeholder\nProject: {project_root}\nWave: {wave}"


def _operational_rules(locale: str) -> str:
    """Правила работы агента. См. spec §17 disambiguation rules."""
    # TODO: load from locale files
    return "TODO: operational rules"


def _tool_and_skill_metadata() -> str:
    """Краткий catalog 22 tools + 10 skills (только names + descriptions).

    Full schemas подгружаются через tool-search-tool-2025-10-19 beta.
    """
    # TODO: auto-generate from @tool registry
    return "TODO: tool + skill catalog"


def _few_shot_examples(locale: str) -> str:
    """10-15 пар «свободный текст → tool call». См. spec §17 examples."""
    # TODO: load from locale
    return "TODO: few-shot"


def _personality(locale: str) -> str:
    if locale == "ru":
        return (
            "Ты — оркестратор bmad-auto-dev. Отвечай по-русски, кратко, без preamble. "
            "Уточняй destructive ops (stop/kill/delete/rollback) через inline buttons. "
            "Read-only ops (status, list, show) выполняй сразу. "
            "Ambiguous query → один уточняющий вопрос, не предполагай."
        )
    return "You are bmad-orchestrator. Be brief. Confirm destructive ops."
