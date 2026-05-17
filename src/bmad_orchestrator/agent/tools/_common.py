"""Shared helpers for tool implementations (spec §17).

Mock-mode is the default: tools read fixtures/state.db, simulate side-effects
that would be destructive (spawn, kill, merge) by recording intent in state.db.
Real subprocess wiring lives in S3 (runtime/worker_spawn.py).
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import yaml

from bmad_orchestrator.config import Settings, load_settings


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def get_settings() -> Settings:
    """Wrapper so tests can monkeypatch loadsettings."""
    return load_settings()


def _first_existing_dir(*candidates: Path) -> Path:
    """Return the first candidate directory that exists; else the first one.

    Used to support both BMad layouts transparently:
      • Upstream BMad: ``<target>/_bmad/planning-artifacts/``,
        ``<target>/_bmad/implementation-artifacts/``, ``<target>/_bmad/stories/``.
      • Legacy orchestrator scaffold (this repo's earlier convention):
        everything under ``<target>/_bmad-output/planning-artifacts/``.

    When neither exists (fresh project), returns the FIRST candidate so the
    default write location is the preferred (upstream BMad) layout.
    """
    for cand in candidates:
        if cand.is_dir():
            return cand
    return candidates[0]


def _first_existing_file(*candidates: Path) -> Path:
    """File-level twin of ``_first_existing_dir``."""
    for cand in candidates:
        if cand.is_file():
            return cand
    return candidates[0]


def artifacts_dir(settings: Settings | None = None) -> Path:
    """Planning-artifacts directory.

    Prefers upstream BMad ``_bmad/planning-artifacts/``; falls back to
    legacy orchestrator ``_bmad-output/planning-artifacts/`` when only
    the latter exists. Kept under the historic name for back-compat with
    existing callers; semantics = «planning artefacts directory».
    """
    s = settings or get_settings()
    return _first_existing_dir(
        s.target_project / "_bmad" / "planning-artifacts",
        s.target_project / s.artifacts_dir_name / "planning-artifacts",
    )


def implementation_dir(settings: Settings | None = None) -> Path:
    """Implementation-artifacts directory (sprint-status, story journals)."""
    s = settings or get_settings()
    return _first_existing_dir(
        s.target_project / "_bmad" / "implementation-artifacts",
        s.target_project / s.artifacts_dir_name / "implementation-artifacts",
    )


def stories_dir(settings: Settings | None = None) -> Path:
    """Stories directory. Upstream BMad keeps stories in a flat ``_bmad/stories/``
    folder; legacy orchestrator nested them under planning-artifacts."""
    s = settings or get_settings()
    return _first_existing_dir(
        s.target_project / "_bmad" / "stories",
        s.target_project / s.artifacts_dir_name / "planning-artifacts" / "stories",
    )


def runs_dir(settings: Settings | None = None) -> Path:
    """Runs directory (worker JSONL events). Both layouts agree on
    ``_bmad-output/runs/`` — this path is created during execution, not by
    planning-phase BMad skills."""
    s = settings or get_settings()
    return s.target_project / s.artifacts_dir_name / "runs"


def worktree_root(settings: Settings | None = None) -> Path:
    """Base directory holding mock worktrees (parent of target_project).

    Real layout per spec §10: sibling `<proj>-wt-N`. In mock-mode we keep
    everything under target_project/.worktrees/.
    """
    s = settings or get_settings()
    return s.target_project / ".worktrees"


def memory_dir(settings: Settings | None = None) -> Path:
    s = settings or get_settings()
    return s.orchestrator_home / ".claude" / "memory"


def text_ok(text: str) -> dict[str, Any]:
    """Build standard MCP tool reply payload."""
    return {"content": [{"type": "text", "text": text}]}


def json_ok(payload: dict[str, Any] | list[Any]) -> dict[str, Any]:
    """Reply with JSON-serialized payload — keeps tool output machine-parseable."""
    return {
        "content": [
            {"type": "text", "text": json.dumps(payload, ensure_ascii=False, default=str)}
        ]
    }


def error(message: str, *, code: str = "tool_error") -> dict[str, Any]:
    payload = {"error": code, "message": message}
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
        "isError": True,
    }


def sprint_status_path(settings: Settings | None = None) -> Path:
    """Resolve sprint-status.yaml across both BMad layouts.

    Upstream BMad puts it under ``implementation-artifacts/``; the legacy
    orchestrator scaffold wrote it to ``planning-artifacts/``. Probe in
    priority: implementation (upstream) → planning (legacy). When neither
    file exists, default write target = implementation under upstream layout.
    """
    s = settings or get_settings()
    return _first_existing_file(
        s.target_project / "_bmad" / "implementation-artifacts" / "sprint-status.yaml",
        s.target_project / "_bmad-output" / "implementation-artifacts" / "sprint-status.yaml",
        s.target_project / s.artifacts_dir_name / "planning-artifacts" / "sprint-status.yaml",
        # Legacy in-orchestrator scaffold path — kept last for back-compat.
        s.target_project / "_bmad" / "planning-artifacts" / "sprint-status.yaml",
    )


def read_sprint_status_yaml(settings: Settings | None = None) -> dict[str, Any]:
    """Parse sprint-status.yaml. Returns empty dict if missing."""
    path = sprint_status_path(settings)
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return cast(dict[str, Any], data)


def write_sprint_status_yaml(data: dict[str, Any], settings: Settings | None = None) -> None:
    """Overwrite sprint-status.yaml. Caller is responsible for flock in real use.

    Writes to whichever location ``sprint_status_path`` resolved. If neither
    candidate file exists yet, the first candidate (upstream BMad layout) is
    used and its parent directory is created.
    """
    path = sprint_status_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


_FRONTMATTER_RE = re.compile(r"^-\s+\*\*([a-z_]+):\*\*\s*(.*?)\s*$", re.MULTILINE)
_LIST_ITEM_RE = re.compile(r"^\s*-\s+(.+?)\s*$", re.MULTILINE)
# H1 title: ``# Story 3.1: Lifecycle state machine`` → title="Lifecycle state machine".
# Mock fixture uses ``# Story 1-1-tenant-signup`` (no colon) → title="" (no human title).
_STORY_TITLE_RE = re.compile(
    r"^\s*#\s*Story\s+[\w.\-]+\s*:\s*(.+?)\s*$", re.MULTILINE
)


def parse_story_md(md_text: str) -> dict[str, Any]:
    """Extract frontmatter-like key/value pairs from BMad story markdown.

    Expected format (canonical, see tests/fixtures/.../stories/*.md):

        # Story 1-1-tenant-signup

        - **epic:** 1
        - **status:** ready-for-dev
        - **risk:** low
        - **estimated_tokens:** 40000
        - **estimated_minutes:** 20
        - **touches_files:**
          - src/tenant/signup.py
        - **touches_shared:** []
        - **depends_on:** []
        - **security_critical:** false
        - **requires_human:** false
    """
    out: dict[str, Any] = {}
    # Inline keys
    for key, val in _FRONTMATTER_RE.findall(md_text):
        val = val.strip()
        if val == "" or val.endswith(":"):
            # multi-line list follows; resolved below
            continue
        out[key] = _coerce_scalar(val)

    # Multi-line list values: lines following "- **key:**" with no inline value.
    list_keys: list[tuple[str, int]] = []
    for m in re.finditer(r"^-\s+\*\*([a-z_]+):\*\*\s*$", md_text, re.MULTILINE):
        list_keys.append((m.group(1), m.end()))

    for key, start in list_keys:
        # Collect "  - item" lines until first non-indented or blank-ish row.
        tail = md_text[start:]
        items: list[str] = []
        for line in tail.splitlines()[1:]:
            if not line.strip():
                break
            stripped = line.lstrip()
            if not stripped.startswith("- "):
                break
            items.append(stripped[2:].strip())
        out[key] = items

    # Title — H1 `# Story X.Y: <title>`; only set when colon-separated form is present.
    m_title = _STORY_TITLE_RE.search(md_text)
    if m_title:
        out["title"] = m_title.group(1).strip()
    return out


def _coerce_scalar(val: str) -> Any:
    if val in ("true", "false"):
        return val == "true"
    if val == "[]":
        return []
    if val.isdigit():
        return int(val)
    try:
        return float(val) if "." in val else val
    except ValueError:
        return val


def list_stories(settings: Settings | None = None) -> list[dict[str, Any]]:
    """Enumerate stories from fixtures/target.

    Returns parsed dicts merged with id (filename stem) + path. Uses
    ``stories_dir`` which probes both upstream (``_bmad/stories/``) and
    legacy (``_bmad-output/planning-artifacts/stories/``) layouts.

    `epic_id` is derived from the filename via :func:`normalize_story_id` so
    both kebab (``3-1-lifecycle-state-machine`` → epic ``3``) and dotted
    (``3.1`` → epic ``3``) story-file naming conventions yield the same epic.
    Sprint-status remains the canonical source when it carries explicit epic
    mapping; the file-derived value is the fallback consumed by
    :class:`bmad_orchestrator.runtime.dag_planner.DagPlanner`.
    """
    # Local import to avoid circular dep: bmad_format → _common is fine, but the
    # opposite path is created only when DagPlanner enrichment is invoked.
    from bmad_orchestrator.runtime.bmad_format import normalize_story_id

    base = stories_dir(settings)
    if not base.exists():
        return []
    stories: list[dict[str, Any]] = []
    for path in sorted(base.glob("*.md")):
        meta = parse_story_md(path.read_text(encoding="utf-8"))
        meta["id"] = path.stem
        meta["file_path"] = str(path)
        dotted = normalize_story_id(path.stem)
        epic_from_file = dotted.split(".", 1)[0] if "." in dotted else dotted
        meta.setdefault("epic_id", epic_from_file)
        stories.append(meta)
    return stories


def jsonl_tail(path: Path, n: int) -> list[dict[str, Any]]:
    """Read last N JSON lines from file. Returns empty list if missing."""
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        lines = f.readlines()
    for line in lines[-max(n, 0):]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def append_jsonl(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")


def worker_jsonl_path(worktree: str, settings: Settings | None = None) -> Path:
    """Best-effort mapping worktree → JSONL events file.

    Mock layout: <runs_dir>/<wave>/<worktree-basename>.events.jsonl.
    Real layout TBD in S3.
    """
    s = settings or get_settings()
    name = Path(worktree).name
    # Wave inferred from settings if not present; fall back to "default".
    wave = os.environ.get("BMAD_CURRENT_WAVE", "default")
    return runs_dir(s) / wave / f"{name}.events.jsonl"


def is_pid_alive(pid: int) -> bool:
    """psutil-free probe; returns False on OSError."""
    if pid <= 0:
        return False
    try:
        import psutil
    except ImportError:
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
    return bool(psutil.pid_exists(pid))


__all__ = [
    "append_jsonl",
    "artifacts_dir",
    "error",
    "get_settings",
    "implementation_dir",
    "is_pid_alive",
    "json_ok",
    "jsonl_tail",
    "list_stories",
    "memory_dir",
    "now_iso",
    "parse_story_md",
    "read_sprint_status_yaml",
    "runs_dir",
    "sprint_status_path",
    "stories_dir",
    "text_ok",
    "worker_jsonl_path",
    "worktree_root",
    "write_sprint_status_yaml",
]
