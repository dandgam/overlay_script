"""Destructive-command classifier per spec §3.5."""

from __future__ import annotations

DESTRUCTIVE_COMMANDS: frozenset[tuple[str, ...]] = frozenset(
    {
        ("stop",),
        ("policy-rollback",),
        ("skill-update",),
        ("self-learning", "rollback"),
        ("multi",),
        ("run",),
    }
)


def is_destructive(full_path: tuple[str, ...]) -> bool:
    return full_path in DESTRUCTIVE_COMMANDS


__all__ = ["DESTRUCTIVE_COMMANDS", "is_destructive"]
