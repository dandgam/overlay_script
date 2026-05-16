"""Git worktree primitive ops (spec §6.2 sibling layout).

Layout:
  /home/server/odyssey/             # main worktree
  /home/server/odyssey-wt-1/        # worktree 1
  /home/server/odyssey-wt-2/        # worktree 2

NOT nested under .worktrees/ (that was earlier spec, superseded by handoff §6.2).
"""

from __future__ import annotations

from pathlib import Path


def make_worktree_path(target_project: Path, n: int) -> Path:
    """Build sibling path. e.g. /home/server/odyssey + 1 -> /home/server/odyssey-wt-1."""
    return target_project.parent / f"{target_project.name}-wt-{n}"


# v1 follow-up: async git worktree add / remove via gitpython or subprocess
