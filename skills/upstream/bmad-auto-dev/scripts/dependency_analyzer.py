#!/usr/bin/env python3
"""dependency_analyzer.py — Parse epics.md + sprint-status.yaml → next ready story.

CLI:
    python3 dependency_analyzer.py --next [--wave 0a]
    python3 dependency_analyzer.py --validate

Output (JSON, single line):
    {"story_id": "1.1", "wave": "0a", "deps_met": true}
    {"all_done": true}

Stories are scanned in document order. DEFERRED stories (per inline `# DEFERRED`
marker on the value line OR `# ... DEFERRED ...` section comment that
propagates within the same `# ===` block) are treated as already satisfied
when used as deps and skipped when picking the next story.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

STORY_HEADING_RE = re.compile(
    r"^#{4,5}\s+Story\s+(?P<id>\d+(?:\.\d+[a-z]?))\s*:",
    re.MULTILINE,
)
DEPS_RE = re.compile(r"^\*\*Dependencies:\*\*\s*(?P<value>.+?)\s*$", re.MULTILINE)
WAVE_RE = re.compile(r"^\*\*Wave:\*\*\s*(?P<value>\S+)", re.MULTILINE)
DEP_REF_RE = re.compile(r"Story\s+(\d+(?:\.\d+[a-z]?))")
STATUS_RE = re.compile(
    r"^\s{2,}(?P<key>[a-z0-9][a-z0-9-]+):\s*(?P<value>[a-z][a-z-]*)\s*(?P<comment>#.*)?$"
)

DEFAULT_EPICS = Path(
    os.environ.get("BMAD_EPICS_FILE") or "_bmad/planning-artifacts/epics.md"
)
DEFAULT_STATUS = Path(
    os.environ.get("BMAD_SPRINT_STATUS")
    or "_bmad/implementation-artifacts/sprint-status.yaml"
)


def parse_epics(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    matches = list(STORY_HEADING_RE.finditer(text))
    stories: dict[str, dict] = {}
    for i, m in enumerate(matches):
        story_id = m.group("id")
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

        stories[story_id] = {"wave": wave, "deps": deps, "order": i}
    return stories


def key_to_story_id(key: str) -> str | None:
    """Map sprint-status YAML key → story_id (or None for epic/retrospective keys).

    Examples:
        '1-1-rust-workspace-scaffold'                -> '1.1'
        '1-8a-postgres-rls-guc-migration'            -> '1.8a'
        'story-0-0-legal-consultation-152-187-fz'    -> '0.0'
        'epic-1' / 'epic-13-retrospective'           -> None
    """
    if key.startswith("epic-"):
        return None
    parts = key.split("-")
    if key.startswith("story-") and len(parts) >= 3:
        return f"{parts[1]}.{parts[2]}"
    if len(parts) >= 2:
        first, second = parts[0], parts[1]
        if first.isdigit() and re.fullmatch(r"\d+[a-z]?", second):
            return f"{first}.{second}"
    return None


def parse_sprint_status(path: Path) -> dict:
    """Return {story_id: {status, deferred, key}}."""
    text = path.read_text(encoding="utf-8")
    statuses: dict[str, dict] = {}
    in_dev_status = False
    deferred_mode = False

    for line in text.splitlines():
        stripped = line.strip()

        if stripped == "development_status:":
            in_dev_status = True
            continue
        if not in_dev_status:
            continue

        if stripped.startswith("#"):
            if stripped.startswith(("# =", "# -")):
                deferred_mode = False
            elif "DEFERRED" in stripped:
                deferred_mode = True
            continue

        if not stripped:
            continue

        m = STATUS_RE.match(line)
        if not m:
            continue

        key = m.group("key")
        story_id = key_to_story_id(key)
        if story_id is None:
            continue

        inline_comment = m.group("comment") or ""
        deferred = deferred_mode or ("DEFERRED" in inline_comment)
        statuses[story_id] = {
            "status": m.group("value"),
            "deferred": deferred,
            "key": key,
        }
    return statuses


def next_ready(stories: dict, statuses: dict, wave_filter: str | None = None) -> dict:
    ordered = sorted(stories.items(), key=lambda x: x[1]["order"])

    found_pending = False
    for story_id, story in ordered:
        st = statuses.get(story_id)
        if st is None or st["deferred"] or st["status"] == "done":
            continue
        found_pending = True

        if wave_filter and not (story["wave"] or "").startswith(wave_filter):
            continue

        deps_met = True
        for dep_id in story["deps"]:
            dep_st = statuses.get(dep_id)
            if dep_st is None:
                deps_met = False
                break
            if dep_st["deferred"]:
                continue
            if dep_st["status"] != "done":
                deps_met = False
                break

        if deps_met:
            return {"story_id": story_id, "wave": story["wave"], "deps_met": True}

    if not found_pending:
        return {"all_done": True}
    return {"all_done": True}


def validate(stories: dict, statuses: dict) -> list[str]:
    warnings: list[str] = []
    for story_id, story in stories.items():
        for dep in story["deps"]:
            if dep not in stories:
                warnings.append(f"Story {story_id} depends on unknown story {dep}")
        if story_id not in statuses:
            warnings.append(f"Story {story_id} in epics.md missing from sprint-status.yaml")
    return warnings


def main() -> None:
    p = argparse.ArgumentParser(description="Find next ready BMad story by dependencies")
    p.add_argument("--next", action="store_true", help="print next ready story as JSON")
    p.add_argument("--validate", action="store_true", help="warn on unknown story refs")
    p.add_argument("--wave", help="filter by wave prefix (e.g. 0a, 1a, 1b)")
    p.add_argument("--epics", type=Path, default=DEFAULT_EPICS, help="path to epics.md")
    p.add_argument(
        "--status", type=Path, default=DEFAULT_STATUS, help="path to sprint-status.yaml"
    )
    args = p.parse_args()

    if not args.next and not args.validate:
        p.print_help()
        sys.exit(2)

    stories = parse_epics(args.epics)
    statuses = parse_sprint_status(args.status)

    if args.validate:
        warnings = validate(stories, statuses)
        for w in warnings:
            print(f"WARN: {w}", file=sys.stderr)
        sys.exit(1 if warnings else 0)

    if args.next:
        print(json.dumps(next_ready(stories, statuses, args.wave), ensure_ascii=False))


if __name__ == "__main__":
    main()
