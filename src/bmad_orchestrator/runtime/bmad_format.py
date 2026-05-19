"""BMad-format sprint-status parser (initiative dag_planner_bmad_compat — B1).

Real-world BMad-Method projects (e.g. Odyssey) write sprint-status.yaml in a
flat-keyed layout that the orchestrator-scaffold initial parser did not understand:

Upstream BMad layout
--------------------
```yaml
development_status:
  epic-1: done
  1-1-rust-workspace-scaffold: done
  1-17b-mv-fallback-conditional: deferred
  epic-3: in-progress
  3-1-lifecycle-state-machine: done  # 2026-05-17: manual override after review
  3-2-dpa-click-accept-v1: done
  3-3-hard-block-trigger-enforcement: backlog
  epic-1-retrospective: done
```

Legacy orchestrator-scaffold layout
-----------------------------------
```yaml
epics:
  3:
    status: in-progress
    stories:
      3.1: done
      3.2: in-progress
      3.3: ready-for-dev
```

Both must round-trip into the unified `SprintStatus` shape consumed by
`runtime.dag_planner` (which iterates ``sprint_status["epics"]``):

```python
{
    "epics": {
        "<epic_id>": {
            "status": "<token>",
            "stories": {"<X.Y[a-z]*>": "<token>"},
        }
    }
}
```

Public surface (per spec §4 B1):

* :func:`parse_sprint_status_bmad` — entry point, autodetects schema.
* :func:`normalize_story_id` — kebab/dotted → canonical dotted form.
* :func:`extract_status_token` — strip embedded prose comments / trailing tokens.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

KNOWN_STATUSES: frozenset[str] = frozenset(
    {
        "done",
        "in-progress",
        "ready-for-dev",
        "backlog",
        "review",
        "deferred",
        "optional",
    }
)

_EPIC_KEY_RE = re.compile(r"^epic-(\d+)$")
_EPIC_RETRO_KEY_RE = re.compile(r"^epic-(\d+)-retrospective$")
_STORY_KEY_RE = re.compile(r"^(?:story-)?(\d+)-(\d+[a-z]*)(?:-.*)?$")
_DOTTED_STORY_RE = re.compile(r"^(\d+)\.(\d+[a-z]*)")
_KEBAB_STORY_RE = re.compile(r"^(?:story-)?(\d+)-(\d+[a-z]*)")
_BMAD_PROBE_RE = re.compile(r"^(?:epic-\d+|(?:story-)?\d+-\d+)")


def normalize_story_id(raw: str) -> str:
    """Return canonical dotted story id (``"3.1"``, ``"1.17b"``).

    Accepted inputs:

    * Already dotted: ``"3.1"``, ``"1.17b"`` (kept verbatim).
    * Kebab with optional prose tail: ``"3-1-lifecycle-state-machine"`` → ``"3.1"``.
    * Optional ``story-`` prefix: ``"story-0-0-legal-consultation-..."`` → ``"0.0"``.

    Unrecognized inputs are returned as-is so a caller can still surface the
    original value in warnings without mutating it.
    """
    if not isinstance(raw, str):
        return str(raw)
    text = raw.strip()
    m = _DOTTED_STORY_RE.match(text)
    if m:
        return f"{m.group(1)}.{m.group(2)}"
    m = _KEBAB_STORY_RE.match(text)
    if m:
        return f"{m.group(1)}.{m.group(2)}"
    return text


def resolve_sprint_status_key(raw: str, sprint_keys: Any) -> str | None:
    """Find the sprint-status key matching *raw* story id.

    Looks up ``raw`` in ``sprint_keys`` (any iterable of strings):

    1. Exact match (case-insensitive) → return that key verbatim.
    2. Else fold both sides through :func:`normalize_story_id` (dotted form,
       e.g. ``"1.3"``) and collect every sprint key whose normalized form
       matches. This bridges a dotted spawned id (``"1.3"``) to a
       kebab-with-prose composite key (``"1-3-fastapi-app-lifespan-health"``).
       - exactly one match → return it.
       - multiple matches (e.g. ``"1-3-foo"`` and ``"1-3-bar"`` both reduce to
         ``"1.3"``) → log a ``sprint_status_key_ambiguous`` warning and return
         the lexicographically first key, so resolution stays deterministic.
    3. Else return ``None`` (caller decides fallback).

    Used by the real-pilot mark-done loop (NEW-3) to bridge dotted spawned ids
    to the kebab-with-prose keys actually present in sprint-status.yaml.
    """
    if not isinstance(raw, str) or not raw:
        return None
    keys = [k for k in (sprint_keys or []) if isinstance(k, str)]
    if not keys:
        return None
    raw_lower = raw.lower()
    for k in keys:
        if k.lower() == raw_lower:
            return k
    raw_norm = normalize_story_id(raw)
    matches = sorted(k for k in keys if normalize_story_id(k) == raw_norm)
    if not matches:
        return None
    if len(matches) > 1:
        logger.warning(
            "sprint_status_key_ambiguous raw=%s normalized=%s candidates=%s "
            "chosen=%s",
            raw,
            raw_norm,
            matches,
            matches[0],
        )
    return matches[0]


def mark_sprint_status_done(snap: dict[str, Any], raw_id: str) -> str | None:
    """Mark the story matching *raw_id* as ``"done"`` in a **raw** sprint-status
    dict, mutating it in place. Returns the resolved key, or ``None`` on miss.

    NEW-3-completion root cause: the real-pilot mark-done loop used to read the
    raw YAML and look only under ``snap["epics"]`` (the legacy
    orchestrator-scaffold nested layout). Real BMad projects (Antares 1a) write
    the upstream flat layout — ``development_status: {1-4-...: done}`` — where
    ``snap["epics"]`` is absent, so EVERY succeeded story fell through to
    ``pilot_mark_done_unresolved`` and ``resolve_sprint_status_key`` (the v2
    fix) was never even reached. This helper dispatches on layout:

    * Legacy ``epics:`` nested → resolve *raw_id* across every epic block's
      ``stories`` dict and flip the match.
    * Upstream BMad flat (``development_status:`` or bare-flat root) → resolve
      *raw_id* against the flat story keys and flip the match.

    The mutated container is always a live reference into *snap*, so the caller
    can ``write_sprint_status_yaml(snap)`` and preserve the original layout.
    """
    if not isinstance(snap, dict):
        return None
    epics = snap.get("epics")
    if isinstance(epics, dict) and epics:
        for epic_block in epics.values():
            if not isinstance(epic_block, dict):
                continue
            stories = epic_block.get("stories")
            if not isinstance(stories, dict):
                continue
            resolved = resolve_sprint_status_key(raw_id, stories.keys())
            if resolved is not None:
                stories[resolved] = "done"
                return resolved
        return None
    flat = _bmad_flat_dict(snap)
    if isinstance(flat, dict):
        story_keys = [
            k for k in flat if isinstance(k, str) and _STORY_KEY_RE.match(k)
        ]
        resolved = resolve_sprint_status_key(raw_id, story_keys)
        if resolved is not None:
            flat[resolved] = "done"
            return resolved
    return None


def extract_status_token(value: Any) -> str:
    """Return the first whitespace-separated token of *value*, stripped of
    Python/YAML comments.

    Examples:

    * ``"done  # 2026-05-17: manual override"`` → ``"done"``
    * ``"in-progress NEEDS-FIX via bmad-code-review"`` → ``"in-progress"``
    * ``"ready-for-dev"`` → ``"ready-for-dev"``
    * ``None`` / ``""`` → ``""``
    """
    if value is None:
        return ""
    text = str(value).split("#", 1)[0].strip()
    if not text:
        return ""
    parts = text.split()
    return parts[0] if parts else ""


def _canonical_status(raw: Any, *, source_key: str) -> str:
    """Extract status token and map unknown values to ``"backlog"`` with a warning.

    Empty/None values are passed through as ``"backlog"`` silently — they appear
    in legacy fixtures and are not a parse error.
    """
    token = extract_status_token(raw)
    if not token:
        return "backlog"
    if token in KNOWN_STATUSES:
        return token
    logger.warning(
        "bmad_format.unknown_status",
        extra={"key": source_key, "raw": str(raw), "token": token},
    )
    return "backlog"


def _ensure_epic(
    epics: dict[str, dict[str, Any]], epic_id: str
) -> dict[str, Any]:
    """Create-or-return the epic bucket with the default ``backlog`` status."""
    return epics.setdefault(epic_id, {"status": "backlog", "stories": {}})


def _bmad_flat_dict(yaml_data: dict[str, Any]) -> dict[str, Any] | None:
    """Return the flat ``{key: status}`` dict if *yaml_data* is upstream BMad.

    Upstream BMad sprint-status.yaml comes in two equivalent shapes:

    1. Wrapped under ``development_status:`` (Odyssey 2026-05+).
    2. Bare (top-level keys are ``epic-N`` / ``N-M-...``) — rare but valid.

    Returns ``None`` when the data does NOT look like BMad — e.g. it is the
    legacy ``epics:`` layout or empty.
    """
    ds = yaml_data.get("development_status")
    if isinstance(ds, dict):
        return ds
    # Bare flat dict at root?
    for key in yaml_data:
        if isinstance(key, str) and _BMAD_PROBE_RE.match(key):
            return yaml_data
    return None


def _normalize_bmad(flat: dict[str, Any]) -> dict[str, Any]:
    """Convert flat BMad dict → unified ``{"epics": {...}}`` shape."""
    epics: dict[str, dict[str, Any]] = {}
    for raw_key, raw_value in flat.items():
        if not isinstance(raw_key, str):
            continue
        key = raw_key.strip()
        if not key:
            continue
        # Retro entries — capture status under epic for visibility, but they
        # don't drive DAG readiness.  Skipping silently keeps the shape clean.
        if _EPIC_RETRO_KEY_RE.match(key):
            continue
        m_epic = _EPIC_KEY_RE.match(key)
        if m_epic:
            epic_id = m_epic.group(1)
            ep = _ensure_epic(epics, epic_id)
            ep["status"] = _canonical_status(raw_value, source_key=key)
            continue
        m_story = _STORY_KEY_RE.match(key)
        if m_story:
            epic_id = m_story.group(1)
            story_id = f"{m_story.group(1)}.{m_story.group(2)}"
            ep = _ensure_epic(epics, epic_id)
            ep["stories"][story_id] = _canonical_status(raw_value, source_key=key)
            continue
        # Genuinely unrecognized key — log once for operator visibility.
        logger.warning(
            "bmad_format.unrecognized_key",
            extra={"key": key, "raw_value": str(raw_value)},
        )
    return {"epics": epics}


def _normalize_legacy(yaml_data: dict[str, Any]) -> dict[str, Any]:
    """Normalize a legacy ``epics:`` nested layout to the unified shape.

    Re-applies status-token extraction and id normalization so downstream
    consumers see one canonical shape regardless of input schema.
    """
    out: dict[str, dict[str, Any]] = {}
    raw_epics = yaml_data.get("epics") or {}
    if not isinstance(raw_epics, dict):
        return {"epics": {}}
    for raw_epic_id, ep_data in raw_epics.items():
        if not isinstance(ep_data, dict):
            continue
        epic_id = str(raw_epic_id).strip()
        if not epic_id:
            continue
        epic_status = _canonical_status(
            ep_data.get("status"), source_key=f"epic-{epic_id}"
        )
        stories: dict[str, str] = {}
        raw_stories = ep_data.get("stories") or {}
        if isinstance(raw_stories, dict):
            for raw_sid, raw_status in raw_stories.items():
                sid = normalize_story_id(str(raw_sid))
                stories[sid] = _canonical_status(
                    raw_status, source_key=f"story-{sid}"
                )
        out[epic_id] = {"status": epic_status, "stories": stories}
    return {"epics": out}


def parse_sprint_status_bmad(yaml_data: Any) -> dict[str, Any]:
    """Tolerant sprint-status parser. Returns unified ``SprintStatus`` shape.

    Probing order:

    1. Top-level ``epics: {...}`` dict → legacy orchestrator-scaffold layout.
    2. ``development_status: {...}`` dict OR bare flat ``epic-N`` / ``N-M-...``
       keys at root → upstream BMad layout.
    3. Anything else (empty, malformed, non-dict) → ``{"epics": {}}``.

    The returned shape is always ``{"epics": {epic_id: {"status": str,
    "stories": {story_id: str}}}}`` with canonical (whitelisted) status tokens
    and dotted story IDs. Unknown status values are downgraded to ``"backlog"``
    with a structured warning emitted on the module logger.
    """
    if not isinstance(yaml_data, dict):
        return {"epics": {}}
    if isinstance(yaml_data.get("epics"), dict):
        return _normalize_legacy(yaml_data)
    flat = _bmad_flat_dict(yaml_data)
    if flat is not None:
        return _normalize_bmad(flat)
    return {"epics": {}}


__all__ = [
    "KNOWN_STATUSES",
    "extract_status_token",
    "mark_sprint_status_done",
    "normalize_story_id",
    "parse_sprint_status_bmad",
    "resolve_sprint_status_key",
]
