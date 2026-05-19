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

    Probes (in priority order):
      1. ``_bmad/planning-artifacts/`` — legacy upstream BMad
      2. ``<artifacts_dir_name>/planning-artifacts/`` — legacy orchestrator scaffold
      3. ``_bmad/output/planning/`` — BMad v6+ layout (Antares-style)
    """
    s = settings or get_settings()
    return _first_existing_dir(
        s.target_project / "_bmad" / "planning-artifacts",
        s.target_project / s.artifacts_dir_name / "planning-artifacts",
        s.target_project / "_bmad" / "output" / "planning",
    )


def implementation_dir(settings: Settings | None = None) -> Path:
    """Implementation-artifacts directory (sprint-status, story journals)."""
    s = settings or get_settings()
    return _first_existing_dir(
        s.target_project / "_bmad" / "implementation-artifacts",
        s.target_project / s.artifacts_dir_name / "implementation-artifacts",
    )


def stories_dir(settings: Settings | None = None) -> Path:
    """Stories directory.

    Probes (in priority order):
      1. ``_bmad/stories/`` — upstream BMad flat layout
      2. ``<artifacts_dir_name>/planning-artifacts/stories/`` — legacy
      3. ``_bmad/output/planning/stories/`` — BMad v6+ layout (Antares-style)
    """
    s = settings or get_settings()
    return _first_existing_dir(
        s.target_project / "_bmad" / "stories",
        s.target_project / s.artifacts_dir_name / "planning-artifacts" / "stories",
        s.target_project / "_bmad" / "output" / "planning" / "stories",
    )


def runs_dir(settings: Settings | None = None) -> Path:
    """Runs directory (worker JSONL events). Both layouts agree on
    ``_bmad-output/runs/`` — this path is created during execution, not by
    planning-phase BMad skills."""
    s = settings or get_settings()
    return s.target_project / s.artifacts_dir_name / "runs"


def worktree_root(settings: Settings | None = None) -> Path:
    """Base directory holding per-story worktrees.

    NEW-27 — worktrees MUST live **outside** the target project tree. A worker's
    ``claude`` resolves slash commands by walking up the directory tree from its
    CWD; a worktree under ``<target>/.worktrees/`` lets the worker reach
    ``<target>/.claude/skills/`` and resolve the *target project's* skills
    instead of Virgil's embedded ones (the NEW-26 trap).

    Default root: ``/var/tmp/virgil-worktrees/<target-basename>`` — ``/var/tmp``
    (persistent, not a tmpfs) has no ``.claude`` ancestor, so the target's and
    the operator's skills are undiscoverable. Override via the
    ``ORCHESTRATOR_WORKTREE_ROOT`` env var (its value is used verbatim as the
    root; per-story worktrees are ``<root>/wt-<story_id>``).
    """
    s = settings or get_settings()
    override = os.environ.get("ORCHESTRATOR_WORKTREE_ROOT")
    if override:
        return Path(override)
    # Deliberate fixed root, not a predictable secret-bearing temp file:
    # worktrees must sit outside every project tree (NEW-27), git worktree add
    # fails loudly on a hijacked path, and no secrets are written here.
    return Path("/var/tmp/virgil-worktrees") / s.target_project.name  # noqa: S108


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
        # BMad v6+ layout (Antares-style) — sprint-status lives alongside stories.
        s.target_project / "_bmad" / "output" / "planning" / "stories" / "sprint-status.yaml",
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

# NEW-28 — real BMad story files declare dependencies in PROSE headers, not in
# the canonical ``- **depends_on:** []`` machine field. Observed shapes (RU
# Antares + EN equivalents):
#     **Зависит от:** 1.1 (skeleton…), 1.2 (…), 1.3 (…)
#     **Блокирует:**  1.4 (…), все Epic 2+ stories
#     **Depends on:** 1.1, 1.3      /      **Blocks:** 1.4
# Without parsing these the DagPlanner sees no edges and parallelizes
# dependent stories (NEW-28 — pilot wave 2a ran 1.3‖1.4 though 1.4 needs 1.3).
_PROSE_DEPENDS_RE = re.compile(
    r"^\s*\*{0,2}\s*(?:Зависит\s+от|Depends\s+on)\s*:?\s*\*{0,2}\s*:?\s*(.+?)\s*$",
    re.MULTILINE | re.IGNORECASE,
)
_PROSE_BLOCKS_RE = re.compile(
    r"^\s*\*{0,2}\s*(?:Блокирует|Blocks)\s*:?\s*\*{0,2}\s*:?\s*(.+?)\s*$",
    re.MULTILINE | re.IGNORECASE,
)
# Parenthetical prose on a dependency line ("1.2 (compose stack — review)")
# often holds noise digits (versions like ``alembic>=1.14``); strip before
# extracting ids.
_PAREN_RE = re.compile(r"\([^()]*\)")
# A story-id token: dotted ``3.1`` / ``1.17b`` or kebab ``3-1`` / ``3-1-slug``.
_STORY_ID_TOKEN_RE = re.compile(r"\b\d+\.\d+[a-z]?\b|\b\d+-\d+(?:-[a-z0-9-]+)?\b")

# NEW-30 — auto-split size signals. Real BMad stories carry no machine
# ``- **estimated_tokens:**`` field; their size lives in the markdown body:
#   * AC headers — ``**AC1 — …**`` / ``### AC 2`` / ``AC3:`` (line-leading).
#   * a ``## Tasks / Subtasks`` checkbox list — ``- [ ] …`` / ``- [x] …``.
# Without these the split heuristic sees an all-zero story and never splits.
_AC_MARKER_RE = re.compile(r"(?im)^\s*[#*]*\s*AC[ \-]?(\d+)\b")
_TASK_CHECKBOX_RE = re.compile(r"(?m)^\s*[-*]\s*\[[ xX]\]")


def _extract_prose_story_ids(md_text: str, header_re: re.Pattern[str]) -> list[str]:
    """Pull story-id tokens from a prose dependency header (NEW-28).

    ``header_re`` matches a ``Зависит от:`` / ``Блокирует:`` (or EN) line and
    captures its value. Parenthetical prose is stripped first so version
    numbers inside it are not mistaken for story ids. Extraction is liberal —
    :func:`build_graph` resolves each token against real nodes and silently
    drops the ones that match nothing, so a stray false token is harmless.
    """
    ids: list[str] = []
    for m in header_re.finditer(md_text):
        line = _PAREN_RE.sub(" ", m.group(1))
        for tok in _STORY_ID_TOKEN_RE.findall(line):
            if tok not in ids:
                ids.append(tok)
    return ids


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

    # NEW-28 — fall back to prose dependency headers when the canonical
    # ``- **depends_on:**`` machine field is absent (real BMad story files).
    # Canonical frontmatter, when present, always wins.
    if not out.get("depends_on"):
        prose_deps = _extract_prose_story_ids(md_text, _PROSE_DEPENDS_RE)
        if prose_deps:
            out["depends_on"] = prose_deps
    if not out.get("blocks"):
        prose_blocks = _extract_prose_story_ids(md_text, _PROSE_BLOCKS_RE)
        if prose_blocks:
            out["blocks"] = prose_blocks

    # NEW-30 — derive auto-split size signals from the markdown body when the
    # canonical machine fields are absent (real BMad story files). AC count is
    # the number of distinct ``AC<n>`` headers; task count is the size of the
    # Tasks/Subtasks checkbox list. Canonical fields, when present, win.
    if "ac_count" not in out:
        ac_nums = {m.group(1) for m in _AC_MARKER_RE.finditer(md_text)}
        if ac_nums:
            out["ac_count"] = len(ac_nums)
    if "task_count" not in out:
        task_n = len(_TASK_CHECKBOX_RE.findall(md_text))
        if task_n:
            out["task_count"] = task_n
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
