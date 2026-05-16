"""Git worktree primitive ops (spec §6.2 sibling layout).

Layout (W1+):
  <target>/.worktrees/wt-<story_id>/   # per-story worktree under target's tree
  <target>/                            # target project root

Cleanup safety (W4): :func:`cleanup_worktree` refuses to remove a path that is
not strictly inside the configured ``.worktrees`` root. Path traversal
(``..``), absolute paths outside the root, and the root itself raise
``ValueError`` rather than ``shutil.rmtree`` blowing away unrelated state.
"""

from __future__ import annotations

import shutil
from pathlib import Path


def make_worktree_path(target_project: Path, n: int) -> Path:
    """Legacy sibling path. e.g. /home/server/odyssey + 1 -> /home/server/odyssey-wt-1.

    Kept for back-compat with earlier specs. New code uses
    ``target_project / ".worktrees" / f"wt-{story_id}"``.
    """
    return target_project.parent / f"{target_project.name}-wt-{n}"


def cleanup_worktree(path: Path | str, *, root: Path | str) -> None:
    """Recursively remove a worktree dir after merge — refuses to escape ``root``.

    Both ``path`` and ``root`` are resolved (symlinks followed) before the
    containment check. Equal-path (``path == root``) and outside-root paths
    raise ``ValueError``. A non-existent ``path`` is a no-op (idempotent).
    """
    p = Path(path).resolve()
    r = Path(root).resolve()

    if p == r:
        raise ValueError(f"refusing to cleanup worktree root itself: {p}")
    try:
        p.relative_to(r)
    except ValueError as exc:
        raise ValueError(
            f"refusing to cleanup worktree outside {r}: {p}"
        ) from exc

    if not p.exists():
        return
    shutil.rmtree(p)


__all__ = ["cleanup_worktree", "make_worktree_path"]
