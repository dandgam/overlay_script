"""S3 — spec_pilot_findings_closure §2 #8: adaptive subprocess timeout.

The bmad-auto-dev runner historically capped each ``claude -p`` call at 1800s
(Patch H). Heavier stories (12+ AC, security-critical) routinely hit
``subprocess_timeout`` mid-flight. We bumped the default to 3600s, added an
ENV override (``BMAD_RUNNER_CLAUDE_TIMEOUT_SEC``), and exposed an adaptive
selector via ``runtime.subprocess_timeout.pick_timeout_sec``.

Coverage (8 tests, exceeds +7 target):

  AC bucketing (3):
    1. 0 AC → DEFAULT (3600)
    2. 1-3 AC → SMALL (1800); 4-8 AC → MEDIUM (3600)
    3. 9+ AC → LARGE (5400)

  Tag override (1):
    4. security-critical tag → LARGE even when AC count is small

  ENV (3):
    5. BMAD_RUNNER_CLAUDE_TIMEOUT_SEC override wins over rules
    6. Non-positive / non-numeric override falls through to rules
    7. BMAD_WORKER_TIMEOUT_SEC clamps both rule-derived and override values

  CLI (1):
    8. CLI prints chosen seconds for a real story file
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from bmad_orchestrator.runtime.subprocess_timeout import (
    DEFAULT_TIMEOUT_SEC,
    LARGE_TIMEOUT_SEC,
    MEDIUM_TIMEOUT_SEC,
    SMALL_TIMEOUT_SEC,
    pick_timeout_sec,
)

# ------------------------------------------------------------------ AC buckets

def test_pick_zero_ac_returns_default() -> None:
    assert pick_timeout_sec({}, env={}) == DEFAULT_TIMEOUT_SEC
    assert pick_timeout_sec({"acceptance": []}, env={}) == DEFAULT_TIMEOUT_SEC


def test_pick_small_and_medium_buckets() -> None:
    small = {"acceptance": ["a", "b", "c"]}  # 3 → small
    medium = {"acceptance": [f"ac-{i}" for i in range(6)]}  # 6 → medium
    assert pick_timeout_sec(small, env={}) == SMALL_TIMEOUT_SEC
    assert pick_timeout_sec(medium, env={}) == MEDIUM_TIMEOUT_SEC


def test_pick_large_bucket_for_9_plus_ac() -> None:
    large = {"acceptance": [f"ac-{i}" for i in range(12)]}
    assert pick_timeout_sec(large, env={}) == LARGE_TIMEOUT_SEC


# ------------------------------------------------------------------ tag override

def test_security_critical_tag_forces_large_regardless_of_ac() -> None:
    story = {"tags": ["security-critical"], "acceptance": ["ac-1", "ac-2"]}
    assert pick_timeout_sec(story, env={}) == LARGE_TIMEOUT_SEC
    # security_critical: true (boolean shape from frontmatter) also triggers.
    bool_form = {"security_critical": True, "acceptance": ["ac-1"]}
    assert pick_timeout_sec(bool_form, env={}) == LARGE_TIMEOUT_SEC


# ------------------------------------------------------------------ ENV

def test_env_override_wins_over_rules() -> None:
    # Plain story would pick SMALL (3 AC), but env override forces 7200.
    small = {"acceptance": ["a", "b", "c"]}
    assert (
        pick_timeout_sec(small, env={"BMAD_RUNNER_CLAUDE_TIMEOUT_SEC": "7200"})
        == 7200
    )


def test_env_override_ignored_when_invalid() -> None:
    small = {"acceptance": ["a", "b", "c"]}
    assert (
        pick_timeout_sec(small, env={"BMAD_RUNNER_CLAUDE_TIMEOUT_SEC": "abc"})
        == SMALL_TIMEOUT_SEC
    )
    assert (
        pick_timeout_sec(small, env={"BMAD_RUNNER_CLAUDE_TIMEOUT_SEC": "0"})
        == SMALL_TIMEOUT_SEC
    )


def test_orchestrator_cap_clamps_both_paths() -> None:
    # Rule-derived: LARGE (5400) clamped to 1000.
    large = {"acceptance": [f"ac-{i}" for i in range(12)]}
    assert (
        pick_timeout_sec(large, env={"BMAD_WORKER_TIMEOUT_SEC": "1000"})
        == 1000
    )
    # Override: 7200 clamped to 3000.
    small = {"acceptance": ["a"]}
    assert (
        pick_timeout_sec(
            small,
            env={
                "BMAD_RUNNER_CLAUDE_TIMEOUT_SEC": "7200",
                "BMAD_WORKER_TIMEOUT_SEC": "3000",
            },
        )
        == 3000
    )


# ------------------------------------------------------------------ CLI

def test_cli_prints_timeout_for_story_file(tmp_path: Path) -> None:
    story = tmp_path / "5.7.md"
    story.write_text(
        "# Story 5.7: hefty\n\n- **epic:** 5\n"
        "- **acceptance:**\n"
        + "".join(f"  - AC {i}\n" for i in range(12))
        + "\n",
        encoding="utf-8",
    )
    out = subprocess.run(
        [
            sys.executable,
            "-m",
            "bmad_orchestrator.runtime.subprocess_timeout",
            "--story-file",
            str(story),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == str(LARGE_TIMEOUT_SEC)
