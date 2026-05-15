"""Story splitting tools (spec §21 + §17 — 2 tools).

Stage 3.6 pre-split check. Эффект +40pp first-try PASS rate, 2-4× wall-clock.
Phase 2 MVP — heuristic only. Phase 2 v1+ — Opus LLM driven.
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool


@tool(
    "check_should_split",
    "Stage 3.6 — check if story should be split (AC>=7 OR hours>=4 OR 3+ layers). Phase 2 MVP=heuristic, v1=Opus LLM.",
    {"story_id": str},
)
async def check_should_split(args: dict[str, Any]) -> dict[str, Any]:
    """Returns SplitDecision: {decision: split|keep, rationale, sub_stories?}.

    Cache lookup per story_id чтобы предотвратить oscillation.
    """
    return {"content": [{"type": "text", "text": "TODO: heuristic check + optional Opus call"}]}


@tool(
    "split_story",
    "Apply split: write sub-stories в sprint-status.yaml (flock-safe), mark parent superseded.",
    {"story_id": str, "sub_stories": list[dict]},
)
async def split_story(args: dict[str, Any]) -> dict[str, Any]:
    """После approval (Phase 2 MVP — manual via Telegram; v1+ — auto).

    1. flock sprint-status.yaml
    2. insert sub-stories with deps
    3. mark parent story 'superseded'
    4. dispatch first dep-free sub-story to Stage 4
    5. write split-decisions/<story_id>.json
    """
    return {"content": [{"type": "text", "text": "TODO: flock + write + dispatch"}]}
