#!/usr/bin/env python3
"""gauntlet_injector.py — Auto-inject 5-lens elicitation enrichment into a BMad story spec.

CLI:
    python3 gauntlet_injector.py --story <id> [--mode quick|deep|auto] \\
        [--epics PATH] [--templates PATH] [--customize PATH] \\
        [--output FILE] [--dry-run]

Modes:
    quick — 1 combined Claude call with all 5 lenses (~1000 words target)
    deep  — 5 separate Claude calls, one per lens         (~1500 words target)
    auto  — select_gauntlet_mode() decides based on epic + story-text tags

Output: enriched markdown (story body + 5 elicitation sections) on stdout
unless --output supplied. `--dry-run` skips the `claude -p` subprocess and
emits the constructed prompt(s) instead — used in tests and smoke runs.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# Stdlib TOML reader (3.11+). bmad-auto-dev targets 3.11+ per scaffold decision.
try:
    import tomllib
except ImportError:  # pragma: no cover — 3.10 fallback path, not exercised in CI
    tomllib = None

DEFAULT_EPICS = Path(
    os.environ.get("BMAD_EPICS_FILE") or "_bmad/planning-artifacts/epics.md"
)
DEFAULT_TEMPLATES = Path(__file__).resolve().parent.parent / "templates" / "gauntlet-prompts.md"
DEFAULT_CUSTOMIZE = Path(__file__).resolve().parent.parent / "customize.toml"

STORY_HEADING_RE = re.compile(
    r"^(?P<hashes>#{4,5})\s+Story\s+(?P<id>\d+(?:\.\d+[a-z]?))\s*:.*$",
    re.MULTILINE,
)

# Template parser: capture `## Template N — Name` followed by the first ``` block.
# We match the header line, then look ahead for ``` ... ``` (non-greedy).
TEMPLATE_BLOCK_RE = re.compile(
    r"^##\s+Template\s+(?P<num>\d+)\s+—\s+(?P<name>.+?)\s*$"
    r"[\s\S]*?"
    r"^```\s*$"
    r"(?P<body>[\s\S]*?)"
    r"^```\s*$",
    re.MULTILINE,
)

DEFAULT_DEEP_EPICS = ["epic-4", "epic-5", "epic-7", "epic-10"]
DEFAULT_DEEP_TAGS = [
    "security-critical",
    "compliance",
    "billing",
    "ai gateway",
    "pii",
    "rls",
    "crypto",
    "credential",
    "auth",
]


# -----------------------------
# Pure functions (unit-tested)
# -----------------------------


def extract_epic_id(story_id: str) -> str:
    """'1.3' -> 'epic-1'; '13.2a' -> 'epic-13'; 'foo' -> 'epic-foo' (defensive)."""
    head = story_id.split(".", 1)[0]
    return f"epic-{head}"


def select_gauntlet_mode(
    epic_id: str,
    story_text: str,
    deep_epics: list[str],
    deep_tags: list[str],
) -> str:
    """Return 'deep' or 'quick' per spec rules.

    Deep triggers (any one wins):
      - epic_id matches deep_epics (case-insensitive)
      - any tag in deep_tags appears in story_text (case-insensitive substring)
    Otherwise 'quick'.
    """
    epic_lower = epic_id.lower()
    if any(e.lower() == epic_lower for e in deep_epics):
        return "deep"
    text_lower = story_text.lower()
    for tag in deep_tags:
        if tag.lower() in text_lower:
            return "deep"
    return "quick"


def parse_templates(path: Path) -> list[dict]:
    """Parse gauntlet-prompts.md → ordered list of {num, name, body} dicts.

    Returns templates in source-file order (Failure Mode → Edge → Pre-mortem
    → Devil's Advocate → Security per spec).
    """
    text = path.read_text(encoding="utf-8")
    templates: list[dict] = []
    for m in TEMPLATE_BLOCK_RE.finditer(text):
        templates.append(
            {
                "num": int(m.group("num")),
                "name": m.group("name").strip(),
                "body": m.group("body").strip(),
            }
        )
    return templates


def load_config(customize_path: Path) -> dict:
    """Load deep_epics and deep_tags from customize.toml [gauntlet] section."""
    if tomllib is None or not customize_path.exists():
        return {"deep_epics": DEFAULT_DEEP_EPICS, "deep_tags": DEFAULT_DEEP_TAGS}
    with customize_path.open("rb") as f:
        data = tomllib.load(f)
    g = data.get("gauntlet", {})
    return {
        "deep_epics": g.get("deep_epics", DEFAULT_DEEP_EPICS),
        "deep_tags": g.get("deep_tags", DEFAULT_DEEP_TAGS),
    }


def extract_story_text(epics_path: Path, story_id: str) -> str:
    """Slice the story block from epics.md by heading id."""
    text = epics_path.read_text(encoding="utf-8")
    matches = list(STORY_HEADING_RE.finditer(text))
    for i, m in enumerate(matches):
        if m.group("id") == story_id:
            start = m.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            return text[start:end].rstrip()
    raise KeyError(f"Story {story_id!r} not found in {epics_path}")


def build_prompts(
    story_id: str,
    story_text: str,
    mode: str,
    templates: list[dict],
) -> list[tuple[str, str]]:
    """Return [(template_name, prompt_body), ...].

    quick mode  → 1 element with concatenated lenses, word_budget=200/section.
    deep mode   → 5 elements, one per template, word_budget=300/section.
    """
    if mode not in ("quick", "deep"):
        raise ValueError(f"mode must be quick|deep, got {mode!r}")
    if len(templates) != 5:
        raise ValueError(f"expected 5 templates, got {len(templates)}")

    if mode == "deep":
        return [
            (
                t["name"],
                t["body"]
                .replace("{story_id}", story_id)
                .replace("{story_text}", story_text)
                .replace("{word_budget}", "300"),
            )
            for t in templates
        ]

    # quick — combine all 5 lenses into one prompt, ask for all 5 sections.
    header = (
        f"You are enriching BMad story {story_id} with FIVE elicitation lenses in "
        "ONE pass. Produce all five Markdown sections back-to-back, in the listed "
        "order, with the exact titles given. Aim for ~200 words per section "
        "(~1000 words total).\n\n"
        "Story spec (cite by AC number, do NOT restate verbatim):\n"
        f"{story_text}\n\n"
        "===== LENSES =====\n"
    )
    parts = [header]
    for i, t in enumerate(templates, 1):
        body = (
            t["body"]
            .replace("{story_id}", story_id)
            .replace("{story_text}", "(see story spec above — do not repeat here)")
            .replace("{word_budget}", "200")
        )
        parts.append(f"--- Lens {i}: {t['name']} ---\n{body}\n")
    return [("combined", "\n".join(parts))]


def merge_enriched(story_text: str, sections: list[tuple[str, str]]) -> str:
    """Concatenate original story spec + horizontal rule + each enriched section.

    `sections` = [(lens_name, enriched_markdown), ...] returned by Claude calls.
    """
    blocks = [story_text.rstrip(), ""]
    for _name, body in sections:
        blocks.append("---")
        blocks.append("")
        blocks.append(body.strip())
        blocks.append("")
    return "\n".join(blocks).rstrip() + "\n"


# -----------------------------
# I/O — subprocess call to claude -p (mockable via --dry-run)
# -----------------------------


def invoke_claude(prompt: str, model: str = "opus", timeout: int = 180) -> str:
    """Run `claude -p <prompt> --model <model>` and capture stdout.

    Returns trimmed stdout. Raises RuntimeError on non-zero exit with the
    stderr tail attached for debugging.
    """
    cmd = ["claude", "-p", prompt, "--model", model]
    proc = subprocess.run(  # noqa: S603 — trusted bin, args list
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"claude -p exit {proc.returncode}: {proc.stderr.strip()[-400:]}"
        )
    return proc.stdout.strip()


# -----------------------------
# CLI
# -----------------------------


def main() -> None:
    p = argparse.ArgumentParser(
        description="Inject 5-lens Gauntlet enrichment into a BMad story spec"
    )
    p.add_argument("--story", required=True, help="story id, e.g. 1.3 or 13.2a")
    p.add_argument(
        "--mode",
        choices=("quick", "deep", "auto"),
        default="auto",
        help="enrichment depth (default: auto-detect per epic/tags)",
    )
    p.add_argument("--epics", type=Path, default=DEFAULT_EPICS, help="path to epics.md")
    p.add_argument(
        "--templates",
        type=Path,
        default=DEFAULT_TEMPLATES,
        help="path to gauntlet-prompts.md",
    )
    p.add_argument(
        "--customize",
        type=Path,
        default=DEFAULT_CUSTOMIZE,
        help="path to customize.toml",
    )
    p.add_argument("--output", type=Path, help="write enriched markdown to file")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="skip claude -p; emit constructed prompts as JSON instead",
    )
    p.add_argument(
        "--print-mode",
        action="store_true",
        help="print selected mode + epic_id as JSON and exit (no enrichment)",
    )
    args = p.parse_args()

    config = load_config(args.customize)
    story_text = extract_story_text(args.epics, args.story)
    epic_id = extract_epic_id(args.story)

    mode = (
        args.mode
        if args.mode in ("quick", "deep")
        else select_gauntlet_mode(
            epic_id, story_text, config["deep_epics"], config["deep_tags"]
        )
    )

    if args.print_mode:
        print(json.dumps({"story_id": args.story, "epic_id": epic_id, "mode": mode}))
        return

    templates = parse_templates(args.templates)
    prompts = build_prompts(args.story, story_text, mode, templates)

    if args.dry_run or os.environ.get("GAUNTLET_DRY_RUN") == "1":
        payload = {
            "story_id": args.story,
            "epic_id": epic_id,
            "mode": mode,
            "prompts": [{"name": n, "body": b} for n, b in prompts],
        }
        out = json.dumps(payload, ensure_ascii=False, indent=2)
    else:
        enriched: list[tuple[str, str]] = []
        for name, prompt in prompts:
            response = invoke_claude(prompt)
            enriched.append((name, response))
        out = merge_enriched(story_text, enriched)

    if args.output:
        args.output.write_text(out, encoding="utf-8")
    else:
        sys.stdout.write(out)
        if not out.endswith("\n"):
            sys.stdout.write("\n")


if __name__ == "__main__":
    main()
