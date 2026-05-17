#!/usr/bin/env python3
"""batch_gate.py — Detect batch boundary in BMad auto-dev pipeline.

CLI:
    python3 batch_gate.py --check
    python3 batch_gate.py --check --last-story 1.7 --batch-count 3

Output (JSON, single line):
    {"boundary": true,  "reason": "batch_size_reached", "size": 10, "cap": 10}
    {"boundary": true,  "reason": "gate_story",         "story_id": "1.7", "detail": "type_marker"}
    {"boundary": true,  "reason": "wave_transition",    "from": "0a", "to": "0b"}
    {"boundary": false, "reason": "no_state"}
    {"boundary": false, "reason": "continue"}

Detection priority (first match wins):
    1. batch_size_reached  — len(state.stories) >= config.batch_size
    2. gate_story          — config gate_story_ids OR `**Type:** ... GATE story` OR title kw
    3. wave_transition     — wave_of(next_ready) != wave_of(last_story_id)
    4. no_state            — state file missing AND no overrides (boundary=false)
    5. continue            — none of the above
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from dependency_analyzer import (  # noqa: E402
    DEP_REF_RE,
    DEPS_RE,
    WAVE_RE,
    next_ready,
    parse_sprint_status,
)

STORY_HEADING_TITLE_RE = re.compile(
    r"^#{4,5}\s+Story\s+(?P<id>\d+(?:\.\d+[a-z]?))\s*:\s*(?P<title>.+?)\s*$",
    re.MULTILINE,
)
TYPE_FIELD_RE = re.compile(r"^\*\*Type:\*\*\s*(?P<value>.+?)\s*$", re.MULTILINE)

DEFAULT_EPICS = Path("_bmad/planning-artifacts/epics.md")
DEFAULT_STATUS = Path("_bmad/implementation-artifacts/sprint-status.yaml")
DEFAULT_STATE = Path("_bmad/auto-dev-state/current-batch.json")
DEFAULT_CONFIG = SCRIPT_DIR.parent / "customize.toml"

DEFAULT_CONFIG_VALUES = {
    "batch_size": 10,
    "wave_boundary": True,
    "gate_stories": True,
    "gate_story_ids": [],
}

GATE_TITLE_KEYWORDS = ("rollback gate", "gate review", "gate story", "checkpoint gate")
GATE_TYPE_RE = re.compile(r"GATE\s+story", re.IGNORECASE)


def load_config(path: Path) -> dict:
    cfg = dict(DEFAULT_CONFIG_VALUES)
    if not path.exists():
        return cfg
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        return cfg
    batch = data.get("batch", {})
    if isinstance(batch.get("size"), int) and batch["size"] > 0:
        cfg["batch_size"] = batch["size"]
    if isinstance(batch.get("wave_boundary"), bool):
        cfg["wave_boundary"] = batch["wave_boundary"]
    if isinstance(batch.get("gate_stories"), bool):
        cfg["gate_stories"] = batch["gate_stories"]
    ids = batch.get("gate_story_ids")
    if isinstance(ids, list):
        cfg["gate_story_ids"] = [str(x) for x in ids if isinstance(x, (str, int, float))]
    return cfg


def parse_epics_extended(path: Path) -> dict:
    """parse_epics + title + type. Same shape as dependency_analyzer.parse_epics with extras."""
    text = path.read_text(encoding="utf-8")
    matches = list(STORY_HEADING_TITLE_RE.finditer(text))
    stories: dict[str, dict] = {}
    for i, m in enumerate(matches):
        story_id = m.group("id")
        title = m.group("title").strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[start:end]

        deps_match = DEPS_RE.search(block)
        if deps_match:
            value = deps_match.group("value").strip()
            deps: list[str] = [] if value.lower() == "none" else DEP_REF_RE.findall(value)
        else:
            deps = []

        wave_match = WAVE_RE.search(block)
        wave = wave_match.group("value") if wave_match else None

        type_match = TYPE_FIELD_RE.search(block)
        type_value = type_match.group("value").strip() if type_match else None

        stories[story_id] = {
            "wave": wave,
            "deps": deps,
            "order": i,
            "title": title,
            "type": type_value,
        }
    return stories


def is_gate_story(story_id: str, stories: dict, gate_ids: list[str]) -> tuple[bool, str]:
    """Return (is_gate, reason_detail)."""
    if story_id in gate_ids:
        return True, "config_list"
    story = stories.get(story_id)
    if story is None:
        return False, "unknown_story"
    type_value = story.get("type") or ""
    if GATE_TYPE_RE.search(type_value):
        return True, "type_marker"
    title_lower = story["title"].lower()
    for kw in GATE_TITLE_KEYWORDS:
        if kw in title_lower:
            return True, f"title_kw={kw}"
    return False, ""


def read_state(path: Path) -> dict:
    if not path.exists():
        return {"stories": [], "last_story_id": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"stories": [], "last_story_id": None}
    if not isinstance(data, dict):
        return {"stories": [], "last_story_id": None}
    stories = data.get("stories", [])
    if not isinstance(stories, list):
        stories = []
    last = data.get("last_story_id")
    if last is not None and not isinstance(last, str):
        last = None
    return {"stories": stories, "last_story_id": last}


def check_boundary(
    state: dict,
    config: dict,
    epics_stories: dict,
    statuses: dict,
) -> dict:
    batch_stories = state.get("stories", [])
    last = state.get("last_story_id")

    if not batch_stories and last is None:
        return {"boundary": False, "reason": "no_state"}

    if len(batch_stories) >= config["batch_size"]:
        return {
            "boundary": True,
            "reason": "batch_size_reached",
            "size": len(batch_stories),
            "cap": config["batch_size"],
        }

    if config["gate_stories"] and last is not None:
        is_gate, detail = is_gate_story(last, epics_stories, config["gate_story_ids"])
        if is_gate:
            return {
                "boundary": True,
                "reason": "gate_story",
                "story_id": last,
                "detail": detail,
            }

    if config["wave_boundary"] and last is not None:
        last_story = epics_stories.get(last)
        last_wave = last_story["wave"] if last_story else None
        nxt = next_ready(epics_stories, statuses)
        if last_wave and not nxt.get("all_done"):
            next_wave = nxt.get("wave")
            if next_wave and next_wave != last_wave:
                return {
                    "boundary": True,
                    "reason": "wave_transition",
                    "from": last_wave,
                    "to": next_wave,
                }

    return {"boundary": False, "reason": "continue"}


def main() -> None:
    p = argparse.ArgumentParser(description="Detect batch boundary in BMad auto-dev pipeline")
    p.add_argument("--check", action="store_true", help="emit boundary decision as JSON")
    p.add_argument("--last-story", help="override last_story_id (testing/manual)")
    p.add_argument("--batch-count", type=int, help="override batch story count (testing)")
    p.add_argument("--epics", type=Path, default=DEFAULT_EPICS)
    p.add_argument("--status", type=Path, default=DEFAULT_STATUS)
    p.add_argument("--state-file", type=Path, default=DEFAULT_STATE)
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = p.parse_args()

    if not args.check:
        p.print_help()
        sys.exit(2)

    config = load_config(args.config)
    epics_stories = parse_epics_extended(args.epics)
    statuses = parse_sprint_status(args.status)
    state = read_state(args.state_file)

    if args.last_story is not None:
        state["last_story_id"] = args.last_story
    if args.batch_count is not None:
        state["stories"] = [f"_synthetic_{i}" for i in range(args.batch_count)]

    print(json.dumps(check_boundary(state, config, epics_stories, statuses), ensure_ascii=False))


if __name__ == "__main__":
    main()
