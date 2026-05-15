"""Branch isolation enforcer (spec §9 layer 3).

Workers physically cannot touch main — they live in feature/story-X branches inside worktrees.
This module validates that merge targets are NEVER `main`/`master` directly without explicit human approval.
"""

from __future__ import annotations

FORBIDDEN_DIRECT_MERGE_TARGETS: tuple[str, ...] = ("main", "master")


def validate_merge_target(target_branch: str, has_human_approval: bool) -> tuple[bool, str]:
    """Returns (allowed, reason)."""
    if target_branch in FORBIDDEN_DIRECT_MERGE_TARGETS and not has_human_approval:
        return False, f"Direct merge to {target_branch} requires human checkpoint (spec §8.3)"
    return True, "ok"
