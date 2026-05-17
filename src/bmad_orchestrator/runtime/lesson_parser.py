"""L4 lessons → policy proposals (spec §E8).

Reads retrospective lessons from ``skills/lessons/<project>/wave-<N>.md`` and
extracts ``## Policy proposal: <field>`` blocks into structured proposals.
Proposals are persisted to ``_config/projects/<slug>/policy-proposals.yaml``
for operator review, then applied (interactive or ``--auto-apply``) against
the live policy YAMLs under ``skills/policy/``.

Markdown contract
-----------------

Lesson files MAY contain narrative; only blocks shaped exactly like the
template below are extracted:

    ## Policy proposal: <policy_file>.<field>
    before: <yaml-scalar-or-list>
    after: <yaml-scalar-or-list>
    rationale: <one-line text>         # optional

``<policy_file>`` is one of ``code-review-gates``, ``cost-tuning``,
``retry-policy``. ``<field>`` is a top-level key on the matching pydantic
model (:class:`CodeReviewGates`, :class:`CostTuning`, :class:`RetryPolicy`).
``before`` and ``after`` are parsed via ``yaml.safe_load`` so JSON-flavoured
lists, numbers, and strings all round-trip.

Audit + rollback
----------------

Each applied proposal emits an audit event of type
``policy_proposal_applied`` with the full ``before`` / ``after`` payload so a
human can reconstruct the prior state without parsing the lesson file.
"""

from __future__ import annotations

import fcntl
import os
import re
import shutil
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ValidationError

from bmad_orchestrator.agent.safety.audit import record_audit
from bmad_orchestrator.skills_repo import (
    CodeReviewGates,
    CostTuning,
    RetryPolicy,
)

PolicyTarget = Literal["code-review-gates", "cost-tuning", "retry-policy"]

_POLICY_MODELS: dict[PolicyTarget, type[BaseModel]] = {
    "code-review-gates": CodeReviewGates,
    "cost-tuning": CostTuning,
    "retry-policy": RetryPolicy,
}

_PROPOSAL_HEADER_RE = re.compile(
    r"^##\s+Policy\s+proposal:\s*([a-z0-9-]+)\.([a-zA-Z_][a-zA-Z0-9_]*)\s*$"
)

# P1-1 — DoS caps for lesson markdown ingestion. Hostile or accidentally
# pathological lesson files (single 100 MB line, gigabyte file) must fail loud
# before they reach the regex / YAML loader. Limits picked so that even the
# most verbose real retrospectives (~10 KB, few dozen proposals) stay well
# under cap.
_MAX_LINE_LEN: int = 8192
_MAX_FILE_BYTES: int = 1_048_576  # 1 MiB


class LessonParserError(Exception):
    """Base for lesson_parser errors."""


class LessonProposalInvalidError(LessonParserError):
    """Raised on malformed proposal block (missing field, bad value, unknown target)."""


class PolicyApplyError(LessonParserError):
    """Raised when an applied proposal fails schema validation."""


@dataclass(slots=True, frozen=True)
class LessonProposal:
    """One parsed ``## Policy proposal`` block."""

    policy_file: PolicyTarget
    field: str
    before: Any
    after: Any
    rationale: str | None
    source_file: str


@dataclass(slots=True, frozen=True)
class AppliedProposal:
    """Result of successfully applying one proposal."""

    proposal: LessonProposal
    policy_path: Path
    audit_entry: dict[str, Any]


@dataclass(slots=True)
class ApplyResult:
    """Outcome of applying a batch of proposals."""

    applied: list[AppliedProposal] = field(default_factory=list)
    rejected: list[LessonProposal] = field(default_factory=list)
    errors: list[tuple[LessonProposal, str]] = field(default_factory=list)


# ── parsing ────────────────────────────────────────────────────────────────


def _parse_value(raw: str, *, location: str) -> Any:
    raw = raw.strip()
    if not raw:
        raise LessonProposalInvalidError(
            f"{location}: empty value"
        )
    try:
        return yaml.safe_load(raw)
    except yaml.YAMLError as e:
        raise LessonProposalInvalidError(
            f"{location}: value not valid YAML: {raw!r}: {e}"
        ) from e


def parse_lesson_markdown(text: str, *, source: Path) -> list[LessonProposal]:
    """Extract every ``## Policy proposal`` block from ``text``.

    Lines outside a block are ignored (narrative content is allowed). Each
    block must include both ``before:`` and ``after:`` lines; ``rationale:``
    is optional. A malformed block (missing required line, unknown
    ``<policy_file>``, value not parseable as YAML) raises
    :class:`LessonProposalInvalidError` — the parser does not silently drop
    suspect blocks.
    """
    src_str = str(source)

    # P1-1 — DoS cap: reject oversize buffers BEFORE splitlines() materialises
    # a megabyte-long list of pathologically long lines.
    encoded_len = len(text.encode("utf-8"))
    if encoded_len > _MAX_FILE_BYTES:
        raise LessonProposalInvalidError(
            f"{src_str}: lesson markdown exceeds {_MAX_FILE_BYTES} bytes "
            f"({encoded_len} bytes); reject as DoS-shaped input"
        )

    proposals: list[LessonProposal] = []
    lines = text.splitlines()

    for idx, line in enumerate(lines, start=1):
        if len(line) > _MAX_LINE_LEN:
            raise LessonProposalInvalidError(
                f"{src_str}:{idx}: line exceeds {_MAX_LINE_LEN} chars "
                f"({len(line)} chars); reject as DoS-shaped input"
            )

    i = 0
    while i < len(lines):
        line = lines[i]
        m = _PROPOSAL_HEADER_RE.match(line)
        if not m:
            i += 1
            continue
        policy_file_raw, field_name = m.group(1), m.group(2)
        if policy_file_raw not in _POLICY_MODELS:
            raise LessonProposalInvalidError(
                f"{src_str}:{i + 1}: unknown policy file {policy_file_raw!r} "
                f"(expected one of {sorted(_POLICY_MODELS)})"
            )
        policy_file: PolicyTarget = policy_file_raw

        model_cls = _POLICY_MODELS[policy_file]
        if field_name not in model_cls.model_fields:
            raise LessonProposalInvalidError(
                f"{src_str}:{i + 1}: field {field_name!r} not on {policy_file!r}"
            )

        before: Any = _MISSING
        after: Any = _MISSING
        rationale: str | None = None
        block_start = i + 1
        j = i + 1
        while j < len(lines):
            block_line = lines[j].strip()
            if not block_line:
                j += 1
                continue
            if block_line.startswith("##") or block_line.startswith("# "):
                break
            if ":" not in block_line:
                j += 1
                continue
            key, _, value = block_line.partition(":")
            key = key.strip().lower()
            location = f"{src_str}:{j + 1}"
            if key == "before":
                before = _parse_value(value, location=location)
            elif key == "after":
                after = _parse_value(value, location=location)
            elif key == "rationale":
                rationale = value.strip() or None
            else:
                # unknown key inside a block — ignore (allow free-form notes)
                pass
            j += 1

        if before is _MISSING:
            raise LessonProposalInvalidError(
                f"{src_str}:{block_start}: proposal {policy_file}.{field_name} "
                "missing 'before:' line"
            )
        if after is _MISSING:
            raise LessonProposalInvalidError(
                f"{src_str}:{block_start}: proposal {policy_file}.{field_name} "
                "missing 'after:' line"
            )

        proposals.append(
            LessonProposal(
                policy_file=policy_file,
                field=field_name,
                before=before,
                after=after,
                rationale=rationale,
                source_file=src_str,
            )
        )
        i = j

    return proposals


_MISSING = object()


def parse_lessons_dir(lessons_dir: Path) -> list[LessonProposal]:
    """Aggregate proposals from every ``*.md`` under ``lessons_dir``.

    Missing directory → empty list (a project with no retrospective output
    is not an error). Files are processed in sorted order so the resulting
    list is deterministic across runs.
    """
    if not lessons_dir.is_dir():
        return []
    out: list[LessonProposal] = []
    for md in sorted(lessons_dir.glob("*.md")):
        # P1-1 — stat() the file first; avoid reading multi-GB blob into RAM.
        size = md.stat().st_size
        if size > _MAX_FILE_BYTES:
            raise LessonProposalInvalidError(
                f"{md}: lesson file size {size} bytes exceeds "
                f"{_MAX_FILE_BYTES}; reject as DoS-shaped input"
            )
        text = md.read_text(encoding="utf-8")
        out.extend(parse_lesson_markdown(text, source=md))
    return out


# ── proposals YAML persistence ─────────────────────────────────────────────


def proposals_yaml_path(slug: str, *, orchestrator_home: Path) -> Path:
    """Resolve the proposals review YAML path under ``_config/projects/<slug>/``."""
    if not slug or "/" in slug or "\\" in slug or slug in {".", ".."}:
        raise LessonProposalInvalidError(f"invalid project slug: {slug!r}")
    return orchestrator_home / "_config" / "projects" / slug / "policy-proposals.yaml"


def _serialise_proposal(p: LessonProposal) -> dict[str, Any]:
    return {
        "policy_file": p.policy_file,
        "field": p.field,
        "before": p.before,
        "after": p.after,
        "rationale": p.rationale,
        "source_file": p.source_file,
    }


def _atomic_yaml_write(path: Path, payload: Any) -> None:
    """Tempfile + fsync + os.replace (same pattern as live_tuning).

    Cross-process safety: an advisory ``fcntl.flock(LOCK_EX)`` on a
    per-target sidecar lockfile (``<parent>/.<name>.lock``) serialises
    concurrent writers (orchestrator + CLI ``policy-apply`` /
    ``policy-rollback``) so the last writer wins cleanly instead of
    interleaving tempfile renames.
    """
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    serialised = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
    lock_path = parent / f".{path.name}.lock"
    with open(lock_path, "w", encoding="utf-8") as lock_fh:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        tmp_fd, tmp_name = tempfile.mkstemp(
            prefix=path.name + ".", suffix=".tmp", dir=str(parent)
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                fh.write(serialised)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, path)
        except Exception:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            raise


def save_proposals_yaml(
    proposals: Iterable[LessonProposal],
    *,
    slug: str,
    orchestrator_home: Path,
) -> Path:
    """Persist ``proposals`` to ``_config/projects/<slug>/policy-proposals.yaml``."""
    path = proposals_yaml_path(slug, orchestrator_home=orchestrator_home)
    payload = {
        "project_slug": slug,
        "proposals": [_serialise_proposal(p) for p in proposals],
    }
    _atomic_yaml_write(path, payload)
    return path


def load_proposals_yaml(slug: str, *, orchestrator_home: Path) -> list[LessonProposal]:
    """Load persisted proposals; missing file → empty list."""
    path = proposals_yaml_path(slug, orchestrator_home=orchestrator_home)
    if not path.exists():
        return []
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise LessonProposalInvalidError(
            f"policy-proposals.yaml not valid YAML at {path}: {e}"
        ) from e
    if not isinstance(raw, dict):
        raise LessonProposalInvalidError(
            f"policy-proposals.yaml must be a mapping at {path}"
        )
    raw_props = raw.get("proposals") or []
    if not isinstance(raw_props, list):
        raise LessonProposalInvalidError(
            f"policy-proposals.yaml 'proposals' must be a list at {path}"
        )
    out: list[LessonProposal] = []
    for entry in raw_props:
        if not isinstance(entry, dict):
            raise LessonProposalInvalidError(
                f"proposal entry must be a mapping at {path}"
            )
        policy_file = entry.get("policy_file")
        if policy_file not in _POLICY_MODELS:
            raise LessonProposalInvalidError(
                f"proposal at {path}: unknown policy_file {policy_file!r}"
            )
        # P0-4 — defence-in-depth: reject proposals whose ``field`` does not
        # exist on the pydantic model. Without this guard a hand-edited or
        # parser-corrupted proposals YAML could inject ``"../etc/passwd"`` or
        # similar non-model keys; pydantic ``model_validate`` would still pass
        # (extra=ignore default) and the bogus key would silently survive in
        # the dumped YAML.
        field_name = str(entry.get("field", ""))
        if field_name not in _POLICY_MODELS[policy_file].model_fields:
            raise LessonProposalInvalidError(
                f"proposal at {path}: unknown field {field_name!r} "
                f"for policy_file {policy_file!r}"
            )
        out.append(
            LessonProposal(
                policy_file=policy_file,
                field=field_name,
                before=entry.get("before"),
                after=entry.get("after"),
                rationale=(
                    str(entry["rationale"])
                    if entry.get("rationale") is not None
                    else None
                ),
                source_file=str(entry.get("source_file", "")),
            )
        )
    return out


# ── apply ─────────────────────────────────────────────────────────────────


def policy_file_path(policy_file: PolicyTarget, *, skills_root: Path) -> Path:
    """Resolve on-disk YAML path for ``policy_file`` under ``skills_root``."""
    return skills_root / "policy" / f"{policy_file}.yaml"


def _load_policy_yaml(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise PolicyApplyError(
            f"policy YAML not valid at {path}: {e}"
        ) from e
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise PolicyApplyError(f"policy YAML must be a mapping at {path}")
    return raw


# P1-2 — policy YAML backup + rollback ────────────────────────────────────

# Keep at most this many ``.yaml.bak-<ts>`` per policy file.  Older backups
# are unlinked after each successful apply so the policy directory does not
# grow unbounded over the lifetime of a project.
_POLICY_BACKUP_KEEP: int = 3
_POLICY_BACKUP_RE = re.compile(r"^(?P<name>[a-z0-9-]+)\.yaml\.bak-(?P<ts>[0-9TZ-]+)$")


def _backup_timestamp(now: datetime | None = None) -> str:
    """UTC timestamp suitable for both directory filenames and CLI args."""
    when = now or datetime.now(UTC)
    return when.strftime("%Y%m%dT%H%M%SZ")


def _policy_backup_path(policy_path: Path, ts: str) -> Path:
    """Resolve the backup path for a given policy file + timestamp."""
    return policy_path.with_name(f"{policy_path.name}.bak-{ts}")


def _list_policy_backups(policy_path: Path) -> list[Path]:
    """All backups for ``policy_path`` ordered oldest→newest by timestamp suffix."""
    parent = policy_path.parent
    if not parent.is_dir():
        return []
    prefix = f"{policy_path.name}.bak-"
    backups = [p for p in parent.iterdir() if p.name.startswith(prefix)]
    backups.sort(key=lambda p: p.name)
    return backups


def _prune_old_backups(policy_path: Path, *, keep: int = _POLICY_BACKUP_KEEP) -> None:
    backups = _list_policy_backups(policy_path)
    if len(backups) <= keep:
        return
    for old in backups[: len(backups) - keep]:
        try:
            old.unlink()
        except OSError:
            # Best-effort: a failed prune is not worth aborting the apply.
            pass


def _snapshot_backup(policy_path: Path, ts: str) -> Path | None:
    """Copy the current policy file aside as ``.bak-<ts>``.

    Returns ``None`` if there is nothing to back up (first-ever apply on a
    fresh policy file). Caller MUST invoke this *before* writing the new
    policy contents so the on-disk copy still represents the pre-change
    state.
    """
    if not policy_path.exists():
        return None
    backup_path = _policy_backup_path(policy_path, ts)
    shutil.copy2(policy_path, backup_path)
    return backup_path


def rollback_policy(
    *, skills_root: Path, proposal_id: str
) -> tuple[Path, Path]:
    """Restore the policy file pointed to by ``proposal_id``.

    ``proposal_id`` is the timestamp suffix that ``apply_proposal`` stamped
    onto the backup file (``<policy>.yaml.bak-<id>``). Scans every policy
    YAML under ``skills_root/policy`` for a matching backup; restores it
    into the original policy path via :func:`_atomic_yaml_write`. Returns
    ``(restored_policy_path, backup_used_path)``.

    Raises :class:`PolicyApplyError` when no matching backup exists.
    """
    policy_dir = skills_root / "policy"
    if not policy_dir.is_dir():
        raise PolicyApplyError(
            f"policy directory missing under skills_root: {policy_dir}"
        )
    suffix = f".bak-{proposal_id}"
    matches: list[Path] = []
    for backup in policy_dir.iterdir():
        if backup.is_file() and backup.name.endswith(suffix):
            matches.append(backup)
    if not matches:
        raise PolicyApplyError(
            f"no backup found under {policy_dir} matching proposal_id "
            f"{proposal_id!r}"
        )
    if len(matches) > 1:
        names = sorted(p.name for p in matches)
        raise PolicyApplyError(
            f"ambiguous proposal_id {proposal_id!r}: matched {names}"
        )
    backup = matches[0]
    original_name = backup.name[: -len(suffix)]  # strip ".bak-<ts>"
    restored = backup.with_name(original_name)
    try:
        payload = yaml.safe_load(backup.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise PolicyApplyError(
            f"backup YAML not valid at {backup}: {e}"
        ) from e
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise PolicyApplyError(
            f"backup YAML must be a mapping at {backup}, got {type(payload).__name__}"
        )
    _atomic_yaml_write(restored, payload)
    record_audit(
        "policy_proposal_rolled_back",
        summary=f"restored {restored.name} from backup {backup.name}",
        proposal_id=proposal_id,
        policy_path=str(restored),
        backup_path=str(backup),
    )
    return restored, backup


def apply_proposal(
    proposal: LessonProposal, *, skills_root: Path
) -> AppliedProposal:
    """Atomically apply ``proposal`` against the matching policy YAML.

    Loads the live YAML, replaces ``proposal.field`` with ``proposal.after``,
    validates against the pydantic schema (so out-of-range / wrong-type
    values raise :class:`PolicyApplyError` BEFORE the disk is touched), then
    writes via the same atomic tempfile pattern as live_tuning. Emits a
    ``policy_proposal_applied`` audit event so the rollback hint is durable
    even after the proposals file is regenerated.
    """
    path = policy_file_path(proposal.policy_file, skills_root=skills_root)
    if not path.exists():
        raise PolicyApplyError(f"policy YAML missing: {path}")

    current = _load_policy_yaml(path)
    model_cls = _POLICY_MODELS[proposal.policy_file]

    updated = dict(current)
    updated[proposal.field] = proposal.after
    try:
        validated = model_cls.model_validate(updated)
    except ValidationError as e:
        raise PolicyApplyError(
            f"proposal {proposal.policy_file}.{proposal.field} fails schema: {e}"
        ) from e

    payload = validated.model_dump(mode="python")

    # P1-2 — snapshot the pre-apply YAML to ``<name>.yaml.bak-<ts>`` BEFORE
    # overwriting. Operators get a deterministic rollback target keyed by the
    # same ``ts`` that surfaces in the audit entry; ``_prune_old_backups``
    # caps the on-disk fan-out.
    backup_ts = _backup_timestamp()
    backup_path = _snapshot_backup(path, backup_ts)
    _atomic_yaml_write(path, payload)
    _prune_old_backups(path)

    audit = record_audit(
        "policy_proposal_applied",
        summary=(
            f"{proposal.policy_file}.{proposal.field}: "
            f"{proposal.before!r} → {proposal.after!r}"
        ),
        policy_file=proposal.policy_file,
        field=proposal.field,
        before=proposal.before,
        after=proposal.after,
        rationale=proposal.rationale,
        source_file=proposal.source_file,
        policy_path=str(path),
        proposal_id=backup_ts,
        backup_path=str(backup_path) if backup_path is not None else None,
    )
    return AppliedProposal(proposal=proposal, policy_path=path, audit_entry=audit)


PromptFn = Callable[[LessonProposal], bool]


def apply_proposals_batch(
    proposals: Iterable[LessonProposal],
    *,
    skills_root: Path,
    auto_apply: bool = False,
    prompt: PromptFn | None = None,
) -> ApplyResult:
    """Apply each proposal in order; collect outcomes into :class:`ApplyResult`.

    With ``auto_apply=True`` every proposal is applied without prompting.
    Otherwise ``prompt`` is invoked per proposal — if it returns ``False``
    the proposal is recorded under ``rejected``. A schema-validation failure
    appends to ``errors`` and continues with the next proposal (one bad
    proposal does not abort the batch).
    """
    result = ApplyResult()
    for proposal in proposals:
        accept = True
        if not auto_apply:
            if prompt is None:
                raise ValueError(
                    "non-auto_apply mode requires a 'prompt' callable"
                )
            accept = bool(prompt(proposal))
        if not accept:
            result.rejected.append(proposal)
            continue
        try:
            applied = apply_proposal(proposal, skills_root=skills_root)
            result.applied.append(applied)
        except PolicyApplyError as e:
            result.errors.append((proposal, str(e)))
    return result


__all__ = [
    "AppliedProposal",
    "ApplyResult",
    "LessonParserError",
    "LessonProposal",
    "LessonProposalInvalidError",
    "PolicyApplyError",
    "PolicyTarget",
    "apply_proposal",
    "apply_proposals_batch",
    "load_proposals_yaml",
    "parse_lesson_markdown",
    "parse_lessons_dir",
    "policy_file_path",
    "proposals_yaml_path",
    "rollback_policy",
    "save_proposals_yaml",
]
