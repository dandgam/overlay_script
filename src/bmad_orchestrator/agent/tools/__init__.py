"""Tools catalog (spec §17).

All tools are registered through the `claude_agent_sdk` MCP `@tool` decorator and
wired into `ClaudeAgentOptions(...)` together with the betas
`tool-search-tool-2025-10-19` + `advanced-tool-use-2025-11-20` (see `agent/betas.py`).
The Tool Search Tool beta provides defer-loading semantics at the API level —
only matching tool schemas hydrate when the agent asks for them. Until the SDK
exposes a per-tool `defer_loading=True` parameter on the decorator, the betas
header is the single switch (S1 verified).
"""

from __future__ import annotations

from typing import Any

from bmad_orchestrator.agent.tools import (
    audit,
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

ALL_TOOLS: list[Any] = [
    *state.TOOLS,
    *dag.TOOLS,
    *spawn.TOOLS,
    *control.TOOLS,
    *merge.TOOLS,
    *memory.TOOLS,
    *retro.TOOLS,
    *operational.TOOLS,
    *splitter.TOOLS,
    *escalate.TOOLS,
    *audit.TOOLS,
]


def tool_names() -> list[str]:
    """Return tool names in canonical registration order (used by system_prompt)."""
    return [t.name for t in ALL_TOOLS]


def tool_descriptions() -> dict[str, str]:
    """Map tool name → description. Used by the cached catalog block in system_prompt."""
    return {t.name: t.description for t in ALL_TOOLS}


__all__ = [
    "ALL_TOOLS",
    "audit",
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
    "tool_descriptions",
    "tool_names",
]
