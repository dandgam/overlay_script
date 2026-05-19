"""Worker events propagation — NEW-10 (spec_pilot_findings_closure_v4 §2).

Pilot run #3 left the main ``_bmad-output/runs/default/wt-<id>.events.jsonl``
with a stale mtime: worker-side events written inside the worktree copy never
reached the orchestrator-side runs directory, so the run was undiagnosable.

This module bridges the gap:

* :func:`worktree_events_path` — the worktree-internal events file a worker
  writes to (``<worktree>/_bmad-output/runs/<wave>/<name>.events.jsonl``).
* :func:`main_events_path` — the orchestrator-side events file the supervisor
  reads from (delegates to :func:`worker_jsonl_path`).
* :func:`merge_worktree_events` — append-merge the worktree-internal file into
  the main file, deduplicating already-present events.
* :func:`detect_stage_marker` — recognise a worker stage transition in a
  stdout line so the orchestrator log does not go silent for 30 minutes.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from bmad_orchestrator.agent.tools._common import worker_jsonl_path
from bmad_orchestrator.config import Settings


def _wave() -> str:
    """Current wave label, mirroring :func:`worker_jsonl_path`'s resolution."""
    return os.environ.get("BMAD_CURRENT_WAVE", "default")


def worktree_events_path(worktree: str | Path, wave: str | None = None) -> Path:
    """Events file a worker writes *inside* its worktree copy.

    The worktree is itself a checkout of the target project, so its runs
    directory is ``<worktree>/_bmad-output/runs/<wave>/`` — distinct from the
    orchestrator's own ``runs/`` even though both use the same basename.
    """
    name = Path(worktree).name
    return (
        Path(worktree)
        / "_bmad-output"
        / "runs"
        / (wave or _wave())
        / f"{name}.events.jsonl"
    )


def main_events_path(
    worktree: str | Path, settings: Settings | None = None
) -> Path:
    """Orchestrator-side events file the supervisor tails for ``worktree``."""
    return worker_jsonl_path(str(worktree), settings)


def _dedup_key(line: str) -> str:
    """Stable dedup key for one JSONL line.

    Prefer ``(ts, event_type, story_id)`` when the line parses as a JSON
    object carrying a timestamp; otherwise fall back to the raw line. This
    keeps the merge idempotent across repeated runs while not collapsing
    distinct events that merely lack a ``ts``.
    """
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return line
    if not isinstance(obj, dict):
        return line
    ts = obj.get("ts")
    if ts is None:
        return line
    return f"{ts}\x1f{obj.get('event_type')}\x1f{obj.get('story_id')}"


def merge_worktree_events(source: Path, dest: Path) -> int:
    """Append events from ``source`` into ``dest``, skipping duplicates.

    Returns the number of events appended. A missing ``source`` is not an
    error — it returns ``0`` (a worker may halt before writing anything).
    ``dest`` is created (with parents) only when there is something to write.
    """
    if not source.is_file():
        return 0

    seen: set[str] = set()
    if dest.is_file():
        for line in dest.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines():
            stripped = line.strip()
            if stripped:
                seen.add(_dedup_key(stripped))

    new_lines: list[str] = []
    for line in source.read_text(
        encoding="utf-8", errors="replace"
    ).splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        key = _dedup_key(stripped)
        if key in seen:
            continue
        seen.add(key)
        new_lines.append(stripped)

    if new_lines:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("a", encoding="utf-8") as f:
            for ln in new_lines:
                f.write(ln + "\n")
    return len(new_lines)


# Stage markers a worker echoes to stdout. Matched case-insensitively so the
# orchestrator log can surface a ``worker_stage_progress`` line instead of
# going silent between ``git_worktree_created`` and ``real_pilot_done``.
_STAGE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bstage\s+(\d+)\b", re.IGNORECASE), "stage"),
    (re.compile(r"\bэтап\s+(\d+)\b", re.IGNORECASE), "stage"),
    (re.compile(r"\bverdict[:\s]+(\w+)", re.IGNORECASE), "verdict"),
)


def detect_stage_marker(text: str) -> str | None:
    """Return a short stage label if ``text`` signals a worker transition.

    Used by the completion tailer to emit periodic ``worker_stage_progress``
    log lines so a long-running worker does not leave the orchestrator log
    silent (NEW-10 acceptance: log must not be silent >5 min).
    """
    for pattern, kind in _STAGE_PATTERNS:
        m = pattern.search(text)
        if m:
            return f"{kind}:{m.group(1)}"
    return None


__all__ = [
    "detect_stage_marker",
    "main_events_path",
    "merge_worktree_events",
    "worktree_events_path",
]
