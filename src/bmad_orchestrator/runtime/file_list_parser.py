"""Patch W — story File List parser + scope allow-list (canonical port).

Reads the ``### File List`` section of a story markdown file
(``_bmad/stories/<id>.md``) and turns it into an allow-list of relative
paths that Patch Q (diff-size gate) and Patch R (pre-merge recovery) honour
when filtering or scoping a worker's changes.

Origin: Odyssey Wave 1a batch-3 retrospective. Patch R was auto-committing
scope-creep (lifecycle tests from sibling stories) because ``git add -A``
swept the entire worktree. Patch Q would then revert the over-large diff,
forcing a manual override. Patch W teaches the recovery / gate to restrict
itself to paths the story declared (plus a fixed set of always-in-scope
infrastructure files — sprint-status, deferred-work, the story file itself,
and wave retrospectives).

Reference: ``~/.claude/projects/-home-server-odyssey/memory/
skill_improvement_patch_W_candidate.md`` + Odyssey story 3.3 retrospective.

Format expected in story markdown:

```markdown
### File List

NEW:
- `path/relative/to/repo.md`
- `apps/api/tests/test_xyz.rs`

UPDATE:
- `docs/known-limitations.md`
- `_bmad/implementation-artifacts/sprint-status.yaml`
```

Tolerant to:

* Mixed bullet markers (``-``, ``*``).
* Backticks (`` `path` ``) and bare paths.
* Trailing ``(this file)`` annotations.
* Missing NEW / UPDATE subsections — bullets directly under ``### File List``
  are still collected.
* Other ``### …`` headings closing the section.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

# ── Infrastructure paths always inside scope, regardless of File List ─────
# (Patches that auto-stage residue need these to land — sprint-status row
#  bump, the story file itself, the deferred-work log, and wave retros.)
ALWAYS_IN_SCOPE_PATHS: tuple[str, ...] = (
    "_bmad/implementation-artifacts/sprint-status.yaml",
    "_bmad/implementation-artifacts/deferred-work.md",
)

ALWAYS_IN_SCOPE_GLOBS: tuple[str, ...] = (
    "_bmad/implementation-artifacts/*-retrospective.md",
    "_bmad/implementation-artifacts/retrospective-*.md",
)

_HEADER_FILE_LIST_RE = re.compile(r"^###\s+File\s+List\s*$", re.IGNORECASE)
_HEADER_ANY_RE = re.compile(r"^#{1,6}\s+\S")
_BULLET_RE = re.compile(r"^\s*[-*]\s+(.+?)\s*$")
_BACKTICKED_PATH_RE = re.compile(r"`([^`]+)`")
_TRAILING_NOTE_RE = re.compile(r"\s*\([^)]*\)\s*$")


@dataclass(slots=True, frozen=True)
class FileList:
    """Parsed ``### File List`` section.

    ``new`` and ``update`` retain bucket information for diagnostics; ``all``
    is the de-duplicated union callers normally want.
    """

    new: tuple[str, ...] = ()
    update: tuple[str, ...] = ()
    all: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class AllowList:
    """Story-scoped allow-list for Patch Q / Patch R filtering.

    ``paths`` is an exact-match set (story File List ∪ ALWAYS_IN_SCOPE_PATHS
    ∪ the story file's own path). ``globs`` is matched with ``fnmatch``;
    retrospectives are matched this way because their date suffix varies.
    """

    paths: frozenset[str] = field(default_factory=frozenset)
    globs: tuple[str, ...] = ()

    def contains(self, path: str) -> bool:
        if path in self.paths:
            return True
        for pattern in self.globs:
            if fnmatch.fnmatchcase(path, pattern):
                return True
        return False


def _normalise_path(raw: str) -> str:
    """Strip annotation noise from a bullet line and return the cleaned path.

    Accepts variants:
        ``- `foo/bar.md` (this file)``  → ``foo/bar.md``
        ``- foo/bar.md``                 → ``foo/bar.md``
        ``- foo/bar.md (deferred)``     → ``foo/bar.md``
    """
    s = raw.strip()
    m = _BACKTICKED_PATH_RE.search(s)
    if m:
        return m.group(1).strip()
    s = _TRAILING_NOTE_RE.sub("", s)
    return s.strip().strip("`")


def parse_file_list(story_path: Path) -> FileList:
    """Read the story markdown at ``story_path`` and parse File List.

    Returns an empty ``FileList`` (all tuples empty) when:
        * the file does not exist,
        * the file exists but has no ``### File List`` heading,
        * the section exists but contains no bullets.

    No exception is raised in any of these cases — callers treat "no list"
    as "permissive (no allow-list filtering)" upstream.
    """
    if not story_path.exists() or not story_path.is_file():
        return FileList()
    try:
        text = story_path.read_text(encoding="utf-8")
    except OSError as e:
        log.warning(
            "file_list_read_failed", path=str(story_path), error=str(e)
        )
        return FileList()

    lines = text.splitlines()
    in_section = False
    bucket: str | None = None
    new_paths: list[str] = []
    update_paths: list[str] = []
    unbucketed: list[str] = []

    for line in lines:
        if not in_section:
            if _HEADER_FILE_LIST_RE.match(line):
                in_section = True
            continue

        # Inside section — close on a subsequent heading.
        if _HEADER_ANY_RE.match(line):
            break

        stripped = line.strip()
        if not stripped:
            continue

        # Bucket marker lines like ``NEW:`` / ``UPDATE:`` (case-insensitive).
        lower = stripped.rstrip(":").strip().lower()
        if lower in ("new", "update", "modified", "added"):
            if lower in ("new", "added"):
                bucket = "new"
            else:
                bucket = "update"
            continue

        m = _BULLET_RE.match(line)
        if not m:
            continue
        path = _normalise_path(m.group(1))
        if not path:
            continue
        if bucket == "new":
            new_paths.append(path)
        elif bucket == "update":
            update_paths.append(path)
        else:
            unbucketed.append(path)

    # Build a de-duplicated ``all`` in stable order: new → update → unbucketed.
    seen: set[str] = set()
    all_list: list[str] = []
    for p in (*new_paths, *update_paths, *unbucketed):
        if p in seen:
            continue
        seen.add(p)
        all_list.append(p)

    return FileList(
        new=tuple(new_paths),
        update=tuple(update_paths),
        all=tuple(all_list),
    )


def collect_allow_list(
    target_project: Path,
    story_id: str,
    story_path: Path | None = None,
) -> AllowList:
    """Build the full scope allow-list for a story.

    Composition (in order; later items extend, do not override):

        1. Story File List (``### File List`` from ``story_path`` if given,
           else ``target_project / _bmad/stories/<story_id>.md``).
        2. The story file's own relative path
           (``_bmad/stories/<story_id>.md``).
        3. ``ALWAYS_IN_SCOPE_PATHS`` (sprint-status.yaml + deferred-work.md).
        4. ``ALWAYS_IN_SCOPE_GLOBS`` (retrospectives wildcard).

    ``story_id`` is taken verbatim — caller passes whatever the worker used
    (``"3.3"``, ``"epic-1-story-2"``, …).
    """
    if story_path is None:
        story_path = target_project / "_bmad" / "stories" / f"{story_id}.md"

    listing = parse_file_list(story_path)

    paths: set[str] = set(listing.all)
    paths.add(f"_bmad/stories/{story_id}.md")
    paths.update(ALWAYS_IN_SCOPE_PATHS)

    return AllowList(
        paths=frozenset(paths),
        globs=ALWAYS_IN_SCOPE_GLOBS,
    )


def partition_paths(
    paths: list[str], allow: AllowList
) -> tuple[list[str], list[str]]:
    """Split ``paths`` into ``(in_scope, out_of_scope)`` preserving order."""
    in_scope: list[str] = []
    out_of_scope: list[str] = []
    for p in paths:
        if allow.contains(p):
            in_scope.append(p)
        else:
            out_of_scope.append(p)
    return in_scope, out_of_scope


__all__ = [
    "ALWAYS_IN_SCOPE_GLOBS",
    "ALWAYS_IN_SCOPE_PATHS",
    "AllowList",
    "FileList",
    "collect_allow_list",
    "parse_file_list",
    "partition_paths",
]
