"""CLI path validation helpers (defence-in-depth).

R3 security minors — guard ``--lessons-dir`` / ``--skills-root`` /
``--orchestrator-home`` against:

* Symlink traversal escapes (resolve + compare to a parent root, when applicable).
* System-directory targets (``/etc``, ``/root``, ``/proc``, ``/sys``, ``/dev``,
  ``/boot``, ``/lib``, ``/lib64``, ``/sbin``, ``/bin``, ``/usr/bin``,
  ``/usr/sbin``, ``/usr/lib``, ``/usr/lib64``) — refuse even with explicit
  user intent: orchestrator must never scan/read/write system locations.

The two helpers exposed:
    safe_resolve_path   — canonicalize a path; refuse if it lands in a
                          system-prefix or doesn't exist when required.
    ensure_inside_root  — verify a resolved path is strictly under another
                          (used for ``--lessons-dir`` ⊆ ``--skills-root``).
"""

from __future__ import annotations

from pathlib import Path

import typer

# Deny-list of resolved-path prefixes the orchestrator must never touch. Order
# matters only for readability; the check is membership ("is this a prefix?").
DENY_SYSTEM_PREFIXES: tuple[str, ...] = (
    "/etc",
    "/root",
    "/proc",
    "/sys",
    "/dev",
    "/boot",
    "/lib",
    "/lib64",
    "/sbin",
    "/bin",
    "/usr/bin",
    "/usr/sbin",
    "/usr/lib",
    "/usr/lib64",
    "/var/log",
    "/var/lib",
    "/var/spool",
)


class UnsafePathError(typer.BadParameter):
    """Raised when a CLI path falls in a denied location or escapes its root."""


def _is_under(child: Path, parent: Path) -> bool:
    """True if ``child`` resolves strictly inside ``parent`` (or equals it)."""
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def safe_resolve_path(
    raw: Path,
    *,
    name: str,
    must_exist: bool = False,
) -> Path:
    """Canonicalize ``raw`` and refuse if it lands in a denied system prefix.

    ``must_exist=True`` adds a presence check (used for inputs the caller is
    about to read — e.g. ``--lessons-dir``). When the path doesn't exist yet
    (e.g. a fresh ``--orchestrator-home``) leave the check off.
    """
    resolved = raw.expanduser().resolve()
    str_resolved = str(resolved)
    for prefix in DENY_SYSTEM_PREFIXES:
        if str_resolved == prefix or str_resolved.startswith(prefix + "/"):
            raise UnsafePathError(
                f"{name}={raw!s} resolves to {resolved}, which is inside the "
                f"denied system prefix {prefix!r}. Choose a path outside "
                f"system directories.",
                param_hint=name,
            )
    if must_exist and not resolved.exists():
        raise UnsafePathError(
            f"{name}={raw!s} does not exist (resolved: {resolved}).",
            param_hint=name,
        )
    return resolved


def ensure_inside_root(
    child: Path,
    root: Path,
    *,
    child_name: str,
    root_name: str,
) -> Path:
    """Resolve ``child`` and confirm it sits strictly inside ``root``.

    Both paths are resolved first (symlinks followed) so a benign-looking
    relative path cannot escape via ``..`` or a symlink target. Used for the
    ``--lessons-dir`` ⊆ ``--skills-root`` constraint at policy-apply entry.
    """
    resolved_child = child.expanduser().resolve()
    resolved_root = root.expanduser().resolve()
    if not _is_under(resolved_child, resolved_root):
        raise UnsafePathError(
            f"{child_name}={child!s} resolves to {resolved_child}, which is "
            f"NOT inside {root_name}={resolved_root}. Place lessons under the "
            f"skills root or change --skills-root.",
            param_hint=child_name,
        )
    return resolved_child


__all__ = [
    "DENY_SYSTEM_PREFIXES",
    "UnsafePathError",
    "ensure_inside_root",
    "safe_resolve_path",
]
