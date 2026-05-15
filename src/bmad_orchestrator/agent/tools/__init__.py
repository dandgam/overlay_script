"""Tools catalog (22 tools, spec §17).

ВСЕ tools регистрируются с defer_loading=True (см. spec §18.3 anti-pattern #1).
Полные схемы подгружаются через Anthropic tool-search-tool-2025-10-19 beta только когда агент явно ищет инструмент.
"""

from bmad_orchestrator.agent.tools import (
    control,
    dag,
    escalate,
    memory,
    merge,
    operational,
    retro,
    spawn,
    splitter,
    state,
)

__all__ = [
    "control",
    "dag",
    "escalate",
    "memory",
    "merge",
    "operational",
    "retro",
    "spawn",
    "splitter",
    "state",
]
