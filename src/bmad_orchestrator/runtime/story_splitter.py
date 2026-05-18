"""Story-splitter helpers (Initiative #2 Task 2.1 + 2.2).

Pure-python heuristic + LLM decomposition validator. The LLM tool wrappers in
``agent/tools/splitter.py`` call into these functions so the heuristic lives in
exactly one canonical place; downstream code (DagPlanner, watchdog,
story-splitter skill) may import from here directly without going through the
``@tool`` Claude SDK boundary.

Placed under ``runtime/`` (not ``agent/skills/dag-planner/``) because Python
packages cannot contain ``-``; ``agent/skills/dag-planner/SKILL.md`` references
this module by import path.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

# ── Heuristic thresholds (Phase 2 MVP; see spec §21 + Initiative #2 Task 2.1) ─

SPLIT_TOKEN_THRESHOLD = 5_000
SPLIT_FILE_COUNT_THRESHOLD = 10
SPLIT_AC_THRESHOLD = 7
SPLIT_MINUTES_THRESHOLD = 240
SPLIT_LAYER_THRESHOLD = 3

LAYER_PREFIXES: tuple[tuple[str, str], ...] = (
    ("src/", "backend"),
    ("frontend/", "frontend"),
    ("js/", "frontend"),
    ("tests/", "tests"),
    ("workers/", "workers"),
    ("crm-rs/", "rust"),
    ("agent/", "python_agent"),
    ("bot/", "python_agent"),
)


@dataclass(frozen=True)
class SplitDecision:
    """Result of evaluating the split heuristic against one story."""

    decision: str  # "split" | "keep"
    rules_hit: tuple[str, ...]
    ac_count: int
    estimated_minutes: int
    estimated_tokens: int
    touches_files_count: int
    layers: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "rationale": list(self.rules_hit),
            "ac_count": self.ac_count,
            "minutes": self.estimated_minutes,
            "tokens": self.estimated_tokens,
            "files_count": self.touches_files_count,
            "layers": list(self.layers),
        }


def classify_layers(files: Iterable[str]) -> set[str]:
    """Map file paths to architectural layer labels via prefix match."""
    out: set[str] = set()
    for f in files:
        for prefix, label in LAYER_PREFIXES:
            if f.startswith(prefix):
                out.add(label)
                break
    return out


def count_acceptance_criteria(story_meta: dict[str, Any]) -> int:
    """Count AC bullets from a story's ``_acceptance_text`` block.

    Bullets recognised: ``-``, ``*``, ``•``. Falls back to 0 when the field
    is absent (story frontmatter parser did not populate AC text).
    """
    text = story_meta.get("_acceptance_text") or ""
    if isinstance(text, str) and text:
        return sum(
            1 for ln in text.splitlines() if ln.strip().startswith(("-", "*", "•"))
        )
    return 0


def _coerce_int(value: Any) -> int:
    """Coerce frontmatter value to int; non-numeric → 0 (defensive)."""
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def evaluate_split(story: dict[str, Any]) -> SplitDecision:
    """Apply Phase 2 MVP heuristic. Pure function — no I/O, no LLM calls."""
    touches = list(story.get("touches_files") or []) + list(
        story.get("touches_shared") or []
    )
    layers = classify_layers(touches)
    minutes = _coerce_int(story.get("estimated_minutes"))
    tokens = _coerce_int(story.get("estimated_tokens"))
    ac_count = count_acceptance_criteria(story)
    file_count = len(touches)

    rules: list[str] = []
    if ac_count >= SPLIT_AC_THRESHOLD:
        rules.append(f"ac>={SPLIT_AC_THRESHOLD} ({ac_count})")
    if minutes >= SPLIT_MINUTES_THRESHOLD:
        rules.append(f"minutes>={SPLIT_MINUTES_THRESHOLD} ({minutes})")
    if tokens >= SPLIT_TOKEN_THRESHOLD:
        rules.append(f"tokens>={SPLIT_TOKEN_THRESHOLD} ({tokens})")
    if file_count >= SPLIT_FILE_COUNT_THRESHOLD:
        rules.append(f"files>={SPLIT_FILE_COUNT_THRESHOLD} ({file_count})")
    if len(layers) >= SPLIT_LAYER_THRESHOLD:
        rules.append(f"layers>={SPLIT_LAYER_THRESHOLD} ({sorted(layers)})")

    return SplitDecision(
        decision="split" if rules else "keep",
        rules_hit=tuple(rules),
        ac_count=ac_count,
        estimated_minutes=minutes,
        estimated_tokens=tokens,
        touches_files_count=file_count,
        layers=tuple(sorted(layers)),
    )


def should_split(story: dict[str, Any]) -> bool:
    """Boolean shortcut matching the spec §Initiative #2 Task 2.1 signature."""
    return evaluate_split(story).decision == "split"


# ── LLM decomposition schema (Initiative #2 Task 2.2) ─────────────────────────

REQUIRED_SUB_STORY_KEYS: tuple[str, ...] = ("id", "title", "deps_on")
MIN_SUBS = 2
MAX_SUBS = 5


class DecompositionError(ValueError):
    """LLM decomposition payload failed schema validation."""


def validate_decomposition(
    payload: Any,
    *,
    parent_id: str | None = None,
    parent_touches_files: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Validate Opus-emitted sub-story list. Returns the same list on success.

    Failure modes raised as ``DecompositionError`` (spec §21.7 fallback path):
    wrong outer type, count outside ``[MIN_SUBS, MAX_SUBS]``, missing required
    key, non-string id, duplicate id, id colliding with parent, ``deps_on`` not
    ``list[str]``, dep referring to unknown peer, cycle in deps_on graph.

    Review findings H-4/H-5 also enforce:
    * ``ac`` (if present) is a list of length ≤ 5 — matches the prompt cap so
      a misbehaving LLM that emits a 12-AC sub-story does not defeat the
      splitting purpose.
    * ``touches_files`` is pairwise disjoint across sub-stories — the whole
      point of structured splitting is to guarantee disjoint write sets so
      squash-merge is conflict-free.
    * If ``parent_touches_files`` is supplied, the union of sub-story
      ``touches_files`` must be a subset — sub-stories cannot widen the parent
      File List into new modules the parent never promised to change.
    """
    if not isinstance(payload, list):
        raise DecompositionError(f"expected list, got {type(payload).__name__}")
    if not (MIN_SUBS <= len(payload) <= MAX_SUBS):
        raise DecompositionError(
            f"sub_stories count {len(payload)} outside [{MIN_SUBS},{MAX_SUBS}]"
        )

    seen_ids: set[str] = set()
    validated: list[dict[str, Any]] = []

    for idx, item in enumerate(payload):
        if not isinstance(item, dict):
            raise DecompositionError(f"sub_stories[{idx}] is not a dict")
        for k in REQUIRED_SUB_STORY_KEYS:
            if k not in item:
                raise DecompositionError(
                    f"sub_stories[{idx}] missing required key {k!r}"
                )
        sid = item["id"]
        if not isinstance(sid, str) or not sid:
            raise DecompositionError(f"sub_stories[{idx}].id must be non-empty str")
        if sid in seen_ids:
            raise DecompositionError(f"duplicate sub_story id: {sid!r}")
        if parent_id is not None and sid == parent_id:
            raise DecompositionError(f"sub_story id {sid!r} collides with parent")
        seen_ids.add(sid)
        title = item.get("title")
        if not isinstance(title, str) or not title:
            raise DecompositionError(f"sub_stories[{idx}].title must be non-empty str")
        deps = item.get("deps_on", [])
        if not isinstance(deps, list) or any(not isinstance(d, str) for d in deps):
            raise DecompositionError(
                f"sub_stories[{idx}].deps_on must be list[str]"
            )
        # Review finding H-5 — AC cap matches the prompt's ``AC per
        # sub-story <= 5`` rule. Defensively check type even if key absent.
        if "ac" in item:
            ac = item["ac"]
            if not isinstance(ac, list):
                raise DecompositionError(
                    f"sub_stories[{idx}].ac must be list, got "
                    f"{type(ac).__name__}"
                )
            if len(ac) > 5:
                raise DecompositionError(
                    f"sub_stories[{idx}].ac length {len(ac)} > 5 "
                    f"(splitting must keep each sub-story small)"
                )
        # Review finding H-4 — touches_files schema check; pairwise/subset
        # checks happen after the loop once all sub-stories are validated.
        if "touches_files" in item:
            tf = item["touches_files"]
            if not isinstance(tf, list) or any(
                not isinstance(p, str) for p in tf
            ):
                raise DecompositionError(
                    f"sub_stories[{idx}].touches_files must be list[str]"
                )
        validated.append(item)

    all_ids = {s["id"] for s in validated}
    for s in validated:
        for d in s.get("deps_on", []):
            if d not in all_ids:
                raise DecompositionError(
                    f"sub_story {s['id']!r} depends on unknown peer {d!r}"
                )

    deps_map = {s["id"]: list(s.get("deps_on", [])) for s in validated}
    visited: dict[str, str] = {}

    def dfs(node: str) -> None:
        color = visited.get(node)
        if color == "black":
            return
        if color == "gray":
            raise DecompositionError(f"cycle detected involving {node!r}")
        visited[node] = "gray"
        for d in deps_map.get(node, []):
            dfs(d)
        visited[node] = "black"

    for sid in deps_map:
        dfs(sid)

    # Review finding H-4 — pairwise disjointness of touches_files. Squash
    # merge across sub-stories was advertised as conflict-free precisely
    # because each sub-story owns a disjoint write set. Allow sub-stories to
    # omit ``touches_files`` (legacy planner emissions) but if two declare
    # the same file the decomposition is rejected.
    files_by_sub: dict[str, set[str]] = {
        s["id"]: set(s.get("touches_files", []) or []) for s in validated
    }
    sub_ids = list(files_by_sub)
    for i, a in enumerate(sub_ids):
        for b in sub_ids[i + 1:]:
            overlap = files_by_sub[a] & files_by_sub[b]
            if overlap:
                raise DecompositionError(
                    f"sub_stories {a!r} and {b!r} both touch "
                    f"{sorted(overlap)} — touches_files must be disjoint"
                )

    if parent_touches_files is not None:
        parent_set = set(parent_touches_files)
        union = set().union(*files_by_sub.values()) if files_by_sub else set()
        widened = union - parent_set
        if widened:
            raise DecompositionError(
                f"sub_stories collectively touch {sorted(widened)} which is "
                f"outside the parent File List {sorted(parent_set)}"
            )

    return validated


DECOMPOSITION_PROMPT = """\
You are decomposing a BMad story into 2-5 atomic sub-stories.

Rules:
- Each sub-story must be self-contained (build green, tests pass after merge).
- AC per sub-story <= 5. One architectural layer per sub-story.
- Use explicit `deps_on` (list of sub-story ids in this same decomposition).
- Sub-stories with no deps run in parallel; ordered chain expresses sequence.
- Do NOT split foundation stories or stories with `splittable: false`.

Emit ONLY a JSON list (no prose, no markdown fence), shape:

[
  {
    "id": "<parent_id>-a",
    "title": "<short>",
    "scope": "<one sentence>",
    "ac": ["AC1", "AC2"],
    "estimated_minutes": <int>,
    "deps_on": [],
    "touches_files": ["<rel/path>"]
  }
]

Story metadata follows.
"""


__all__ = [
    "DECOMPOSITION_PROMPT",
    "LAYER_PREFIXES",
    "MAX_SUBS",
    "MIN_SUBS",
    "REQUIRED_SUB_STORY_KEYS",
    "SPLIT_AC_THRESHOLD",
    "SPLIT_FILE_COUNT_THRESHOLD",
    "SPLIT_LAYER_THRESHOLD",
    "SPLIT_MINUTES_THRESHOLD",
    "SPLIT_TOKEN_THRESHOLD",
    "DecompositionError",
    "SplitDecision",
    "classify_layers",
    "count_acceptance_criteria",
    "evaluate_split",
    "should_split",
    "validate_decomposition",
]
