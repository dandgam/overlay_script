"""DAG tools (spec §17 — 3 tools).

build_dag, find_ready_stories, predict_conflicts.
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool


@tool(
    "build_dag",
    "Build DAG of stories for a wave from sprint-status + epics.md frontmatter.",
    {"wave": str},
)
async def build_dag(args: dict[str, Any]) -> dict[str, Any]:
    """Use networkx. Edges: depends_on. Mutex annotations: touches_files, touches_shared."""
    return {"content": [{"type": "text", "text": "TODO: build DAG"}]}


@tool(
    "find_ready_stories",
    "Find next-N stories ready to spawn (deps satisfied + no shared-file mutex with active workers).",
    {"max_n": int},
)
async def find_ready_stories(args: dict[str, Any]) -> dict[str, Any]:
    """Topological roots, filter by mutex + budget headroom."""
    return {"content": [{"type": "text", "text": "TODO: find ready"}]}


@tool(
    "predict_conflicts",
    "Predict potential merge conflicts among given story IDs by overlapping touches_files.",
    {"story_ids": list[str]},
)
async def predict_conflicts(args: dict[str, Any]) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": "TODO: predict conflicts"}]}
