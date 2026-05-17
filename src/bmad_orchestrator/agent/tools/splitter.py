"""Story splitting tools (spec §21 + §17).

check_should_split, split_story.

Phase 2 MVP heuristic lives in ``runtime/story_splitter.py`` — this module
wraps it for LLM (``@tool``) invocation. Keeping the pure helper out of the
SDK-decorator boundary lets downstream code (DagPlanner, watchdog,
story-splitter skill) import the heuristic without pulling in the Claude SDK.
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from bmad_orchestrator.agent.tools._common import (
    artifacts_dir,
    error,
    get_settings,
    json_ok,
    list_stories,
    now_iso,
    read_sprint_status_yaml,
    write_sprint_status_yaml,
)
from bmad_orchestrator.runtime.story_splitter import evaluate_split


@tool(
    "check_should_split",
    "Stage 3.6 — check if story should be split (AC>=7 OR minutes>=240 OR 3+ layers).",
    {"story_id": str},
)
async def check_should_split(args: dict[str, Any]) -> dict[str, Any]:
    story_id = str(args.get("story_id", ""))
    if not story_id:
        return error("missing 'story_id'", code="invalid_arg")

    by_id = {s["id"]: s for s in list_stories()}
    story = by_id.get(story_id)
    if not story:
        return error(f"unknown story: {story_id!r}", code="unknown_story")

    result = evaluate_split(story)
    payload = result.as_dict()
    payload["story_id"] = story_id
    return json_ok(payload)


@tool(
    "split_story",
    "Apply split: write sub-stories in sprint-status.yaml, mark parent superseded.",
    {"story_id": str, "sub_stories": list[dict[str, Any]]},
)
async def split_story(args: dict[str, Any]) -> dict[str, Any]:
    parent_id = str(args.get("story_id", ""))
    sub_stories = args.get("sub_stories") or []
    if not parent_id:
        return error("missing 'story_id'", code="invalid_arg")
    if not isinstance(sub_stories, list) or not sub_stories:
        return error("'sub_stories' must be a non-empty list", code="invalid_arg")

    sprint = read_sprint_status_yaml()
    epics = sprint.setdefault("epics", {})
    parent_epic_id: str | None = None
    for eid, epic in epics.items():
        if parent_id in (epic.get("stories") or {}):
            parent_epic_id = str(eid)
            break
    if parent_epic_id is None:
        return error(f"parent story not found in sprint-status: {parent_id!r}", code="unknown_story")

    sub_ids: list[str] = []
    for sub in sub_stories:
        if not isinstance(sub, dict):
            return error("each sub_story must be a dict", code="invalid_arg")
        sid = str(sub.get("id", "")).strip()
        if not sid:
            return error("sub_story missing 'id'", code="invalid_arg")
        sub_ids.append(sid)
        epics[parent_epic_id].setdefault("stories", {})[sid] = "ready-for-dev"

    epics[parent_epic_id]["stories"][parent_id] = "superseded"
    write_sprint_status_yaml(sprint)

    decision_path = artifacts_dir() / "split-decisions" / f"{parent_id}.json"
    decision_path.parent.mkdir(parents=True, exist_ok=True)
    decision_path.write_text(
        _json_dumps({"parent": parent_id, "sub_ids": sub_ids, "ts": now_iso()}),
        encoding="utf-8",
    )

    return json_ok(
        {
            "parent": parent_id,
            "sub_ids": sub_ids,
            "decision_path": str(decision_path),
            "settings_target": str(get_settings().target_project),
        }
    )


def _json_dumps(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


TOOLS = [check_should_split, split_story]


__all__ = ["TOOLS", "check_should_split", "split_story"]
