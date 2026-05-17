"""Patch Q — diff size gate (canonical port from runner.sh).

Helpers used by ``code_review_subscriber`` to measure the worker's commit
diff size and reject an otherwise-approve verdict if it exceeds a policy
threshold (default 500 lines).

Rationale: a worker that commits a 2000-line change typically has runaway
scope (touched files outside its File List), and `/bmad-code-review` will
approve it on style merits even though the change is too large to safely
land without human triage. The diff size gate is the pre-merge safety net.

Two file allow-lists are honoured (set via the optional ``file_list_paths``
argument so this module stays project-agnostic):

  * File List allow-list (story-scoped) — landed by Patch W (P4), here a stub.
  * Always-allowed paths from the policy yaml — never count against the cap.

For P3 the gate is total-lines vs threshold; the File List filtering arrives
in P4 (Patch W). The hook is here so P4 only needs to populate
``file_list_paths``.

Reference: ~/.claude/skills/bmad-auto-dev/scripts/bmad-auto-dev-runner.sh
``Patch Q`` (lines ~821+ of the original 1100 LOC bash runner).
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path

import structlog
import yaml
from pydantic import BaseModel, Field, ValidationError

from bmad_orchestrator.skills_repo import PolicyInvalidError, PolicyNotFoundError

log = structlog.get_logger(__name__)

DIFF_SIZE_POLICY_PATH_DEFAULT = (
    Path(__file__).resolve().parents[3] / "skills" / "policy" / "diff-size-gate.yaml"
)

# ``--shortstat`` example: " 3 files changed, 42 insertions(+), 17 deletions(-)"
_SHORTSTAT_RE = re.compile(
    r"(?P<ins>\d+)\s+insertion[s]?\(\+\)|(?P<del>\d+)\s+deletion[s]?\(-\)"
)


class DiffSizeGatePolicy(BaseModel):
    """Schema for ``skills/policy/diff-size-gate.yaml``."""

    enabled: bool = True
    max_lines: int = 500
    range_spec: str = "HEAD~1..HEAD"
    always_allowed_paths: list[str] = Field(default_factory=list)
    escalation_text: str = ""


@dataclass(slots=True, frozen=True)
class DiffSizeMetrics:
    insertions: int
    deletions: int
    files_changed: int

    @property
    def total_lines(self) -> int:
        return self.insertions + self.deletions


def load_diff_size_policy(path: Path | None = None) -> DiffSizeGatePolicy:
    """Load + validate the diff-size policy yaml.

    Raises:
        PolicyNotFoundError — file missing.
        PolicyInvalidError  — YAML parse OR schema validation fail.
    """
    p = path or DIFF_SIZE_POLICY_PATH_DEFAULT
    if not p.exists():
        raise PolicyNotFoundError(f"diff-size policy not found: {p}")
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise PolicyInvalidError(f"YAML parse error in {p}: {e}") from e
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise PolicyInvalidError(
            f"top-level structure must be mapping in {p}, got {type(raw).__name__}"
        )
    try:
        return DiffSizeGatePolicy.model_validate(raw)
    except ValidationError as e:
        raise PolicyInvalidError(
            f"diff-size schema validation failed: {e}"
        ) from e


def parse_shortstat(line: str) -> DiffSizeMetrics:
    """Parse one ``git diff --shortstat`` line.

    ``line`` examples:
        " 3 files changed, 42 insertions(+), 17 deletions(-)"
        " 1 file changed, 5 insertions(+)"
        " 1 file changed, 3 deletions(-)"
        ""  (empty → zeros)
    """
    files_match = re.search(r"(\d+)\s+file[s]?\s+changed", line)
    files_changed = int(files_match.group(1)) if files_match else 0
    insertions = 0
    deletions = 0
    for m in _SHORTSTAT_RE.finditer(line):
        ins = m.group("ins")
        dele = m.group("del")
        if ins:
            insertions += int(ins)
        if dele:
            deletions += int(dele)
    return DiffSizeMetrics(
        insertions=insertions, deletions=deletions, files_changed=files_changed
    )


async def measure_diff(
    worktree: Path, range_spec: str = "HEAD~1..HEAD"
) -> DiffSizeMetrics:
    """Run ``git -C worktree diff --shortstat <range_spec>`` and parse it.

    Empty output (no diff or git failure) → zero-metrics — callers treat that
    as "nothing to gate on" rather than escalating.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(worktree),
            "diff",
            "--shortstat",
            range_spec,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (OSError, FileNotFoundError) as e:
        log.warning(
            "diff_size_spawn_failed", worktree=str(worktree), error=str(e)
        )
        return DiffSizeMetrics(0, 0, 0)
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        log.info(
            "diff_size_nonzero",
            worktree=str(worktree),
            returncode=proc.returncode,
            stderr=stderr.decode("utf-8", errors="replace")[:200],
        )
        return DiffSizeMetrics(0, 0, 0)
    text = stdout.decode("utf-8", errors="replace").strip()
    if not text:
        return DiffSizeMetrics(0, 0, 0)
    return parse_shortstat(text)


def gate_verdict(
    metrics: DiffSizeMetrics,
    policy: DiffSizeGatePolicy,
    file_list_paths: list[str] | None = None,
) -> str | None:
    """Return rejection reason string if the gate trips, else None.

    ``file_list_paths`` is a forward-compat hook for Patch W (P4); for P3 it
    is unused — the gate purely measures total-line size against ``max_lines``.

    The function is pure so it can be unit-tested without spawning git.
    """
    _ = file_list_paths  # Patch W hook
    if not policy.enabled:
        return None
    if metrics.total_lines <= policy.max_lines:
        return None
    return (
        f"diff_size_exceeded: total {metrics.total_lines} lines "
        f"(+{metrics.insertions}/-{metrics.deletions}, "
        f"{metrics.files_changed} files) exceeds {policy.max_lines} cap"
    )


__all__ = [
    "DIFF_SIZE_POLICY_PATH_DEFAULT",
    "DiffSizeGatePolicy",
    "DiffSizeMetrics",
    "gate_verdict",
    "load_diff_size_policy",
    "measure_diff",
    "parse_shortstat",
]
