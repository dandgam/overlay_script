"""Anthropic Memory Tool config helper (spec §18.5).

Включается через beta header `context-management-2025-06-27` (agent/betas.py).
SDK сам serializes этот dict в Messages API request — server-side хранит
файловое дерево памяти, агент использует commands view/create/str_replace/...

Финальная wiring в `ClaudeAgentOptions(tools=[memory_tool_definition(), ...])`
живёт в `agent/run.py` (S8 — когда оркестратор реально стартует).

Имя типа (`memory_20250818`) — стабильная beta-version constant, фиксированная
в этом модуле как single source of truth.
"""

from __future__ import annotations

from typing import Final

MEMORY_TOOL_TYPE: Final[str] = "memory_20250818"
MEMORY_TOOL_NAME: Final[str] = "memory"
MEMORY_TOOL_BETA: Final[str] = "context-management-2025-06-27"


def memory_tool_definition() -> dict[str, str]:
    """Return the tool-block dict to add to `ClaudeAgentOptions.tools`.

    Каноничная Anthropic schema: `{"type": "memory_<beta>", "name": "memory"}`.
    Server-managed; локального implementation не требует.
    """
    return {"type": MEMORY_TOOL_TYPE, "name": MEMORY_TOOL_NAME}


__all__ = [
    "MEMORY_TOOL_BETA",
    "MEMORY_TOOL_NAME",
    "MEMORY_TOOL_TYPE",
    "memory_tool_definition",
]
