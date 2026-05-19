"""Adaptive subprocess timeout — Initiative pilot_findings_closure S3 (#8).

The bmad-auto-dev runner historically capped each ``claude -p`` call at 1800s
(Patch H 2026-05-15). On heavier stories (12+ AC, security-critical) Sonnet
legitimately needs 30-60 minutes and the cap killed mid-flight. We:

  1. Raise the default to 3600s (60 min).
  2. Honour ``BMAD_RUNNER_CLAUDE_TIMEOUT_SEC`` env override.
  3. Provide :func:`pick_timeout_sec` for adaptive selection by AC count + tags.
  4. Clamp the runner value to the orchestrator-level
     ``BMAD_WORKER_TIMEOUT_SEC`` so the inner cap never exceeds the outer cap.

Wiring: runner.sh calls
``python3 -m bmad_orchestrator.runtime.subprocess_timeout --story-file <path>``
and exports ``PATCH_H_HARD_CEILING_SECS=<printed value>``.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

# Module-level defaults — pinned constants make tests deterministic.
DEFAULT_TIMEOUT_SEC = 3600
SMALL_TIMEOUT_SEC = 1800  # 0-3 AC
MEDIUM_TIMEOUT_SEC = 3600  # 4-8 AC
LARGE_TIMEOUT_SEC = 5400  # 9+ AC OR security-critical
SECURITY_CRITICAL_TAG = "security-critical"

ENV_RUNNER_TIMEOUT_OVERRIDE = "BMAD_RUNNER_CLAUDE_TIMEOUT_SEC"
ENV_ORCHESTRATOR_CAP = "BMAD_WORKER_TIMEOUT_SEC"


def _ac_count(story: Mapping[str, Any]) -> int:
    """Best-effort AC count from story frontmatter.

    Order of probes (first non-zero wins):
      1. ``ac_count`` int field (explicit).
      2. ``acceptance`` list length.
      3. ``ac`` list length.
      4. ``acceptance_criteria`` list length.
    """
    for key in ("ac_count",):
        v = story.get(key)
        if isinstance(v, int) and v > 0:
            return v
        if isinstance(v, str) and v.isdigit():
            return int(v)
    for key in ("acceptance", "ac", "acceptance_criteria"):
        v = story.get(key)
        if isinstance(v, (list, tuple)):
            n = sum(1 for item in v if str(item).strip())
            if n > 0:
                return n
    return 0


def _story_tags(story: Mapping[str, Any]) -> list[str]:
    raw = story.get("tags")
    if raw is None:
        return []
    if isinstance(raw, str):
        items: Iterable[str] = (s.strip() for s in raw.split(","))
    elif isinstance(raw, (list, tuple, set, frozenset)):
        items = (str(s) for s in raw)
    else:
        return []
    return [s.lower() for s in items if s]


def _is_security_critical(story: Mapping[str, Any]) -> bool:
    if SECURITY_CRITICAL_TAG in _story_tags(story):
        return True
    flag = story.get("security_critical")
    return bool(flag)


def pick_timeout_sec(
    story: Mapping[str, Any] | None,
    *,
    env: Mapping[str, str] | None = None,
) -> int:
    """Return adaptive timeout (seconds) for a single ``claude -p`` call.

    Order of precedence:
      1. ``BMAD_RUNNER_CLAUDE_TIMEOUT_SEC`` env override (positive int).
      2. Security-critical story → LARGE.
      3. 9+ AC → LARGE; 4-8 AC → MEDIUM; 0-3 AC → SMALL.
      4. Empty story → DEFAULT.

    Result is clamped to ``BMAD_WORKER_TIMEOUT_SEC`` if that env var is a
    positive int (runner cap ≤ orchestrator cap invariant).
    """
    env_map = env if env is not None else os.environ

    override_raw = env_map.get(ENV_RUNNER_TIMEOUT_OVERRIDE, "").strip()
    if override_raw.isdigit():
        override = int(override_raw)
        if override > 0:
            return _apply_orchestrator_cap(override, env_map)

    if story is None:
        chosen = DEFAULT_TIMEOUT_SEC
    elif _is_security_critical(story):
        chosen = LARGE_TIMEOUT_SEC
    else:
        n = _ac_count(story)
        if n >= 9:
            chosen = LARGE_TIMEOUT_SEC
        elif n >= 4:
            chosen = MEDIUM_TIMEOUT_SEC
        elif n >= 1:
            chosen = SMALL_TIMEOUT_SEC
        else:
            chosen = DEFAULT_TIMEOUT_SEC

    return _apply_orchestrator_cap(chosen, env_map)


def _apply_orchestrator_cap(value: int, env_map: Mapping[str, str]) -> int:
    raw = env_map.get(ENV_ORCHESTRATOR_CAP, "").strip()
    if raw.isdigit():
        cap = int(raw)
        if cap > 0 and value > cap:
            return cap
    return value


def _cli_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m bmad_orchestrator.runtime.subprocess_timeout",
        description="Pick adaptive claude -p timeout (seconds) per story.",
    )
    parser.add_argument(
        "--story-file",
        type=Path,
        help="Path to story markdown. Frontmatter parsed for ac/tags.",
    )
    args = parser.parse_args(argv)

    story: dict[str, Any] = {}
    if args.story_file and args.story_file.exists():
        try:
            from bmad_orchestrator.agent.tools._common import parse_story_md

            story = parse_story_md(args.story_file.read_text(encoding="utf-8"))
        except Exception:
            story = {}

    sys.stdout.write(str(pick_timeout_sec(story)))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - thin CLI wrapper
    raise SystemExit(_cli_main())


__all__ = [
    "DEFAULT_TIMEOUT_SEC",
    "ENV_ORCHESTRATOR_CAP",
    "ENV_RUNNER_TIMEOUT_OVERRIDE",
    "LARGE_TIMEOUT_SEC",
    "MEDIUM_TIMEOUT_SEC",
    "SECURITY_CRITICAL_TAG",
    "SMALL_TIMEOUT_SEC",
    "pick_timeout_sec",
]
