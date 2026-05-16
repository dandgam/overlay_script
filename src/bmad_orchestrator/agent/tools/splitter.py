"""Story splitting tools (spec §21 + §17).

check_should_split, split_story.

Phase 2 MVP — pure heuristic (см. spec §21):
    split when AC >= 7 OR estimated_minutes >= 4*60 OR touched layers >= 3.

Layers are derived from `touches_files` prefixes:
    src/    -> backend
    js/, frontend/ -> frontend
    tests/  -> tests
    workers/ -> workers
    crm-rs/  -> rust
    agent/, bot/ -> python_agent
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

_LAYER_PREFIXES: list[tuple[str, str]] = [
    ("src/", "backend"),
    ("frontend/", "frontend"),
    ("js/", "frontend"),
    ("tests/", "tests"),
    ("workers/", "workers"),
    ("crm-rs/", "rust"),
    ("agent/", "python_agent"),
    ("bot/", "python_agent"),
]


def _classify_layers(files: list[str]) -> set[str]:
    out: set[str] = set()
    for f in files:
        for prefix, label in _LAYER_PREFIXES:
            if f.startswith(prefix):
                out.add(label)
                break
    return out


def _count_acceptance_criteria(story_meta: dict[str, Any]) -> int:
    text = story_meta.get("_acceptance_text") or ""
    if isinstance(text, str) and text:
        return sum(1 for ln in text.splitlines() if ln.strip().startswith(("-", "*", "•")))
    # fall back to coarse heuristic from estimated_minutes when AC text not parsed
    return 0


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

    layers = _classify_layers(story.get("touches_files") or [])
    minutes = int(story.get("estimated_minutes") or 0)
    ac_count = _count_acceptance_criteria(story)
    rules_hit: list[str] = []
    if ac_count >= 7:
        rules_hit.append(f"ac>=7 ({ac_count})")
    if minutes >= 240:
        rules_hit.append(f"minutes>=240 ({minutes})")
    if len(layers) >= 3:
        rules_hit.append(f"layers>=3 ({sorted(layers)})")

    decision = "split" if rules_hit else "keep"
    return json_ok(
        {
            "story_id": story_id,
            "decision": decision,
            "rationale": rules_hit,
            "ac_count": ac_count,
            "minutes": minutes,
            "layers": sorted(layers),
        }
    )


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
