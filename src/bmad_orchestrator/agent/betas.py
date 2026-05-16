"""Anthropic API beta headers (spec §11.1).

КРИТИЧНО: все 4 заголовка обязательны для оркестратора:
- tool-search-tool-2025-10-19    — Tool Search Tool + defer_loading (§18.2 #2)
- advanced-tool-use-2025-11-20   — advanced tool use patterns
- context-management-2025-06-27  — Memory tool (§18.5)
- interleaved-thinking-2025-05-14 — thinking блоки между tool calls (§19.3 reflexion loop)

Используется в:
- ClaudeAgentOptions при старте мастер-агента (см. agent/run.py)
- Anthropic SDK raw calls (DAG planner, retro subagent)
"""

from __future__ import annotations

from typing import Final

ANTHROPIC_BETA_HEADERS: Final[list[str]] = [
    "tool-search-tool-2025-10-19",
    "advanced-tool-use-2025-11-20",
    "context-management-2025-06-27",
    "interleaved-thinking-2025-05-14",
]
