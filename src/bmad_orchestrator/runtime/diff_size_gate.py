"""Patch Q — diff size gate (canonical port from runner.sh).

Helpers used by ``code_review_subscriber`` to measure the worker's commit
diff size and reject an otherwise-approve verdict if it exceeds a policy
threshold (default 500 lines) OR if files outside the story's File List
allow-list were touched (Patch W, P4).

Rationale: a worker that commits a 2000-line change typically has runaway
scope (touched files outside its File List), and `/bmad-code-review` will
approve it on style merits even though the change is too large to safely
land without human triage. The diff size gate is the pre-merge safety net.

Two safeguards live in this module:

  * **Patch Q** (total-line cap) — `gate_verdict` rejects when
    ``metrics.total_lines > policy.max_lines``.
  * **Patch W** (scope check) — if the caller supplies
    ``out_of_scope_paths`` (computed via :func:`measure_diff_per_file` +
    :func:`partition_per_file` against the story's allow-list), the gate
    rejects with ``scope_violation:`` regardless of size.

Reference: ~/.claude/skills/bmad-auto-dev/scripts/bmad-auto-dev-runner.sh
``Patch Q`` (lines ~821+ of the original 1100 LOC bash runner) +
``~/.claude/projects/-home-server-odyssey/memory/
skill_improvement_patch_W_candidate.md``.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path

import structlog
import yaml
from pydantic import BaseModel, Field, ValidationError

from bmad_orchestrator.runtime.file_list_parser import AllowList
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


@dataclass(slots=True, frozen=True)
class FileDiff:
    """Per-file ``git diff --numstat`` row.

    ``insertions``/``deletions`` are integers; for binary files git reports
    a dash and the parser maps those to zero (binary edits don't contribute
    to the line cap).
    """

    path: str
    insertions: int
    deletions: int


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


async def measure_diff_per_file(
    worktree: Path, range_spec: str = "HEAD~1..HEAD"
) -> list[FileDiff]:
    """Run ``git -C worktree diff --numstat <range_spec>`` and parse rows.

    ``--numstat`` emits one line per file: ``<ins>\\t<del>\\t<path>``.
    Binary files have ``-`` instead of integers; we treat those as zero
    so they appear in the file list (and the scope check) but don't
    contribute to ``total_lines``.

    Empty / failing diff → empty list (caller treats as "nothing to gate").
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(worktree),
            "diff",
            "--numstat",
            range_spec,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (OSError, FileNotFoundError) as e:
        log.warning(
            "diff_per_file_spawn_failed", worktree=str(worktree), error=str(e)
        )
        return []
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        log.info(
            "diff_per_file_nonzero",
            worktree=str(worktree),
            returncode=proc.returncode,
            stderr=stderr.decode("utf-8", errors="replace")[:200],
        )
        return []
    rows: list[FileDiff] = []
    for line in stdout.decode("utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        ins_raw, del_raw, path = parts
        try:
            insertions = int(ins_raw) if ins_raw != "-" else 0
            deletions = int(del_raw) if del_raw != "-" else 0
        except ValueError:
            continue
        rows.append(
            FileDiff(path=path.strip(), insertions=insertions, deletions=deletions)
        )
    return rows


def partition_per_file(
    per_file: list[FileDiff], allow: AllowList
) -> tuple[DiffSizeMetrics, list[str]]:
    """Split ``per_file`` by allow-list membership.

    Returns:
        * Aggregated ``DiffSizeMetrics`` covering ONLY in-scope files
          (in-scope insertions + deletions + files_changed count).
        * Sorted list of out-of-scope paths (deterministic for diagnostics).
    """
    in_ins = 0
    in_del = 0
    in_files = 0
    out_paths: list[str] = []
    for fd in per_file:
        if allow.contains(fd.path):
            in_ins += fd.insertions
            in_del += fd.deletions
            in_files += 1
        else:
            out_paths.append(fd.path)
    metrics = DiffSizeMetrics(
        insertions=in_ins, deletions=in_del, files_changed=in_files
    )
    return metrics, sorted(out_paths)


def gate_verdict(
    metrics: DiffSizeMetrics,
    policy: DiffSizeGatePolicy,
    out_of_scope_paths: list[str] | None = None,
) -> str | None:
    """Return rejection reason string if the gate trips, else None.

    Two rejection signals (in order — first hit wins):

      1. **Patch W — scope_violation** — ``out_of_scope_paths`` non-empty,
         meaning the worker touched files outside the story's File List
         allow-list. Reject even if total lines are small.
      2. **Patch Q — diff_size_exceeded** — in-scope total-line count
         exceeds ``policy.max_lines``.

    ``out_of_scope_paths=None`` skips the scope check (P3 behaviour);
    callers wire it via :func:`measure_diff_per_file` +
    :func:`partition_per_file` against an ``AllowList`` built from the
    story's File List.

    The function stays pure so it can be unit-tested without spawning git.
    """
    if not policy.enabled:
        return None
    if out_of_scope_paths:
        preview = ", ".join(out_of_scope_paths[:5])
        suffix = "" if len(out_of_scope_paths) <= 5 else f", +{len(out_of_scope_paths) - 5} more"
        return (
            f"scope_violation: worker touched {len(out_of_scope_paths)} "
            f"file(s) outside the story's File List allow-list: "
            f"{preview}{suffix}"
        )
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
    "FileDiff",
    "gate_verdict",
    "load_diff_size_policy",
    "measure_diff",
    "measure_diff_per_file",
    "parse_shortstat",
    "partition_per_file",
]
