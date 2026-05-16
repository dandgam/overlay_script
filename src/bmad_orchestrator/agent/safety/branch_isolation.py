"""Branch isolation enforcer (spec §9 layer 3).

Worker физически не может писать в main: ему выдают `git worktree add <path> <branch>`,
где `branch != main` и `path` лежит вне основного репо. Этот модуль валидирует:

1. `validate_merge_target` — direct merge target никогда == main без human approval.
2. `validate_worker_write_path` — worker пишет ТОЛЬКО в свой worktree, не в parent repo
   и не за пределы `target_project`.

`validate_worker_write_path` вызывается perimeter-чеком в spawn_worker tool и из
hooks для всех Edit/Write вызовов worker-процесса.
"""

from __future__ import annotations

from pathlib import Path

FORBIDDEN_DIRECT_MERGE_TARGETS: tuple[str, ...] = ("main", "master")


def validate_merge_target(target_branch: str, has_human_approval: bool) -> tuple[bool, str]:
    """Returns (allowed, reason).

    `has_human_approval=True` — caller передал явное одобрение (флаг трекера
    `Auto merge: true` или Telegram inline-confirmation).
    """
    if target_branch in FORBIDDEN_DIRECT_MERGE_TARGETS and not has_human_approval:
        return False, f"Direct merge to {target_branch} requires human checkpoint (spec §8.3)"
    return True, "ok"


def _resolve(p: Path | str) -> Path:
    return Path(p).resolve() if isinstance(p, str) else p.resolve()


def validate_worker_write_path(
    target_path: Path | str,
    worktree_root: Path | str,
) -> tuple[bool, str]:
    """Validate that the target write path is inside the worker's worktree.

    `target_path` — куда worker хочет писать (Edit/Write file_path).
    `worktree_root` — корень worktree этого worker'а (e.g. /home/server/odyssey-wt-1).

    Возвращает (allowed, reason). Path resolved (symlinks, ..) для anti-traversal.
    """
    try:
        target = _resolve(target_path)
        root = _resolve(worktree_root)
    except OSError as exc:  # pragma: no cover — defensive
        return False, f"path resolution failed: {exc}"

    try:
        target.relative_to(root)
    except ValueError:
        return False, f"write path {target} escapes worktree root {root}"
    return True, "ok"


__all__ = [
    "FORBIDDEN_DIRECT_MERGE_TARGETS",
    "validate_merge_target",
    "validate_worker_write_path",
]
