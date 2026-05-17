"""Patch X — security_review_subscriber (canonical port from Odyssey handoff).

On ``CODE_REVIEW_VERDICT`` (verdict=approve), this subscriber checks whether the
story qualifies as security-critical and, if so, spawns the 4-hunter parallel
``/bmad-security-review`` skill. Hunter aggregate verdict drives behaviour:

  * ``approve`` / ``merge_with_fixes`` → no mutation. ``merge_to_integration_subscriber``
    fires as usual. An audit event ``SECURITY_REVIEW_PASSED`` is emitted with
    the aggregate verdict + findings tail.
  * ``block`` → mutate the in-flight CODE_REVIEW_VERDICT payload:
    ``verdict='reject'`` + append ``security_review_block`` to ``gate_reasons``,
    then emit ``HUMAN_QUERY`` carrying the offending findings.
    ``merge_to_integration_subscriber`` (gates on ``verdict == 'approve'``)
    naturally skips. No new event type needed for the halt path; reuses the
    payload-mutation contract established by Patch C / Patch N / Patch S.

Trigger sources (OR-combined, from ``skills/policy/security-review.yaml``):
  1. Story file frontmatter has ``security_critical: true``.
  2. Story belongs to an epic listed in ``security_critical_epics`` (default
     {3, 4, 5, 7, 9, 10}).
  3. Story spec body OR worker git diff contains any keyword from
     ``security_critical_keywords`` (case-insensitive substring match;
     defaults: auth, jwt, rls, dpa, crypto, billing, pii, audit, hmac, argon,
     session).

Wiring: AFTER ``code_review_subscriber`` and BEFORE
``merge_to_integration_subscriber`` so a BLOCK halts the merge naturally.

Reference: Odyssey handoff 2026-05-17 § 4.1 Patch X candidate
(``/home/server/odyssey/spec/handoffs/handoff-bmad-phase4-gaps-2026-05-17.md``)
and ``/home/server/.claude/skills/bmad-security-review/SKILL.md``.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import structlog
import yaml
from pydantic import BaseModel, Field, ValidationError

from bmad_orchestrator.agent.tools._common import parse_story_md
from bmad_orchestrator.runtime.bmad_format import normalize_story_id
from bmad_orchestrator.runtime.event_loop import Event, EventLoop, EventType
from bmad_orchestrator.skills_repo import PolicyInvalidError, PolicyNotFoundError

log = structlog.get_logger(__name__)

SECURITY_REVIEW_POLICY_PATH_DEFAULT = (
    Path(__file__).resolve().parents[3] / "skills" / "policy" / "security-review.yaml"
)

SECURITY_REVIEW_SKILL_INVOCATION: str = "/bmad-security-review --auto"

VERDICT_APPROVE = "approve"
VERDICT_MERGE_WITH_FIXES = "merge_with_fixes"
VERDICT_BLOCK = "block"
VERDICT_ERROR = "error"

ALL_VERDICTS: frozenset[str] = frozenset(
    {VERDICT_APPROVE, VERDICT_MERGE_WITH_FIXES, VERDICT_BLOCK, VERDICT_ERROR}
)

_VERDICT_LINE_RE = re.compile(
    r"verdict\s*[:\-]\s*[^\w]*\s*(APPROVE|MERGE[\s\-_]?WITH[\s\-_]?FIXES|BLOCK)",
    re.IGNORECASE,
)


class SecurityReviewPolicy(BaseModel):
    """Schema for ``skills/policy/security-review.yaml``."""

    enabled: bool = True
    security_critical_epics: list[int] = Field(
        default_factory=lambda: [3, 4, 5, 7, 9, 10]
    )
    security_critical_keywords: list[str] = Field(
        default_factory=lambda: [
            "auth",
            "jwt",
            "rls",
            "dpa",
            "crypto",
            "billing",
            "pii",
            "audit",
            "hmac",
            "argon",
            "session",
        ]
    )
    block_actions: list[str] = Field(
        default_factory=lambda: ["abandon", "manual_security_fix"]
    )
    escalation_text: str = ""


@dataclass(slots=True, frozen=True)
class SecurityTrigger:
    """Why a story qualifies as security-critical."""

    reason: str  # "frontmatter" | "epic" | "keyword"
    detail: str  # human-readable evidence


# Runner signature: (worktree, story_id, wave) -> (verdict, findings_text).
SecurityReviewRunner = Callable[[Path, str, str], Awaitable[tuple[str, str]]]


def load_security_review_policy(
    path: Path | None = None,
) -> SecurityReviewPolicy:
    """Load + validate the security-review policy yaml.

    Raises:
        PolicyNotFoundError — file missing.
        PolicyInvalidError  — YAML parse OR schema validation fail.
    """
    p = path or SECURITY_REVIEW_POLICY_PATH_DEFAULT
    if not p.exists():
        raise PolicyNotFoundError(f"security-review policy not found: {p}")
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
        return SecurityReviewPolicy.model_validate(raw)
    except ValidationError as e:
        raise PolicyInvalidError(
            f"security-review schema validation failed: {e}"
        ) from e


def extract_epic_from_story_id(story_id: str) -> int | None:
    """Return the integer epic number from a canonical story id, or None.

    Accepts dotted (``"3.1"``, ``"3.2b"``) and kebab (``"3-1-foo"``,
    ``"story-3-1-foo"``) shapes. Returns None for ids that don't lead with a
    digit segment.
    """
    if not isinstance(story_id, str) or not story_id.strip():
        return None
    canonical = normalize_story_id(story_id.strip())
    head = canonical.split(".", 1)[0]
    if head.isdigit():
        return int(head)
    return None


def keyword_match_in_text(text: str, keywords: list[str]) -> list[str]:
    """Return a sorted list of unique keyword hits in ``text`` (case-insensitive)."""
    if not text or not keywords:
        return []
    lower = text.lower()
    hits = {kw for kw in keywords if kw and kw.lower() in lower}
    return sorted(hits)


def is_security_critical(
    *,
    story_id: str,
    frontmatter: dict[str, object],
    story_text: str,
    diff_text: str,
    policy: SecurityReviewPolicy,
) -> SecurityTrigger | None:
    """Return the first trigger that classifies the story as security-critical."""
    flag = frontmatter.get("security_critical")
    if isinstance(flag, bool) and flag:
        return SecurityTrigger(
            reason="frontmatter",
            detail="security_critical: true in story frontmatter",
        )

    epic = extract_epic_from_story_id(story_id)
    if epic is not None and epic in policy.security_critical_epics:
        return SecurityTrigger(
            reason="epic",
            detail=f"epic {epic} in security_critical_epics",
        )

    spec_hits = keyword_match_in_text(story_text, policy.security_critical_keywords)
    if spec_hits:
        return SecurityTrigger(
            reason="keyword",
            detail=f"story spec hits: {', '.join(spec_hits)}",
        )

    diff_hits = keyword_match_in_text(diff_text, policy.security_critical_keywords)
    if diff_hits:
        return SecurityTrigger(
            reason="keyword",
            detail=f"diff hits: {', '.join(diff_hits)}",
        )

    return None


def parse_security_verdict(text: str) -> str | None:
    """Return ``approve|merge_with_fixes|block`` from a verdict line, or None."""
    if not text:
        return None
    m = _VERDICT_LINE_RE.search(text)
    if m is None:
        return None
    word = m.group(1).upper()
    if word == "APPROVE":
        return VERDICT_APPROVE
    if word == "BLOCK":
        return VERDICT_BLOCK
    return VERDICT_MERGE_WITH_FIXES


def parse_security_verdict_from_event(
    ev: dict[str, object],
) -> tuple[str, str] | None:
    """Return ``(verdict, findings_text)`` if event carries verdict info, else None.

    Supports two shapes from ``/bmad-security-review --auto``:
      * Explicit JSON key: ``{"verdict": "BLOCK", "findings": "..."}`` (case-
        insensitive value).
      * Text body (``text`` / ``summary`` / ``content``) containing
        ``Verdict: <APPROVE|MERGE WITH FIXES|BLOCK>``.
    """
    if not isinstance(ev, dict):
        return None
    explicit = ev.get("verdict")
    if isinstance(explicit, str):
        word = explicit.strip().upper().replace("-", " ").replace("_", " ")
        canonical: str | None
        if word == "APPROVE":
            canonical = VERDICT_APPROVE
        elif word == "BLOCK":
            canonical = VERDICT_BLOCK
        elif word in ("MERGE WITH FIXES", "MERGE WITH FIX"):
            canonical = VERDICT_MERGE_WITH_FIXES
        else:
            canonical = None
        if canonical is not None:
            findings = ev.get("findings") or ev.get("summary") or ev.get("text") or ""
            return canonical, str(findings)
    for key in ("findings", "summary", "text", "content"):
        val = ev.get(key)
        if isinstance(val, str):
            v = parse_security_verdict(val)
            if v is not None:
                return v, val
    return None


async def _git_diff_text(worktree: Path) -> str:
    """Return the full diff of HEAD vs HEAD~1 in the worker's worktree.

    Used as one of two keyword-scan inputs. Returns empty string on any git
    failure (caller treats missing diff as "no keyword evidence from diff").
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(worktree),
            "diff",
            "HEAD~1..HEAD",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (OSError, FileNotFoundError) as e:
        log.warning("security_review_diff_spawn_failed", worktree=str(worktree), error=str(e))
        return ""
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return ""
    return stdout.decode("utf-8", errors="replace")


def _read_story_file(worktree: Path, story_id: str) -> tuple[dict[str, object], str]:
    """Return (frontmatter, raw_text) from the story md under the worktree.

    Looks for ``_bmad/stories/<story_id>.md``; falls back to legacy
    ``_bmad-output/planning-artifacts/stories/<story_id>.md``. Missing file →
    empty frontmatter + empty body (the caller still has diff-text + epic-id
    triggers to fall back on).
    """
    candidates = [
        worktree / "_bmad" / "stories" / f"{story_id}.md",
        worktree
        / "_bmad-output"
        / "planning-artifacts"
        / "stories"
        / f"{story_id}.md",
    ]
    for path in candidates:
        if path.exists():
            try:
                raw = path.read_text(encoding="utf-8")
            except OSError as e:
                log.warning(
                    "security_review_story_read_failed",
                    path=str(path),
                    error=str(e),
                )
                return {}, ""
            return parse_story_md(raw), raw
    return {}, ""


async def security_review_subscriber(
    event: Event,
    bus: EventLoop,
    *,
    policy_path: Path | None = None,
    runner: SecurityReviewRunner | None = None,
) -> None:
    """Patch X subscriber — see module docstring.

    Idempotent on non-CODE_REVIEW_VERDICT events and on already-rejected
    verdicts (no point security-reviewing a rejected story).
    """
    if event.type != EventType.CODE_REVIEW_VERDICT:
        return
    payload = event.payload or {}
    if payload.get("verdict") != "approve":
        return

    story_id_raw = payload.get("story_id")
    worktree_raw = payload.get("worktree")
    if not isinstance(story_id_raw, str) or not story_id_raw:
        log.warning("security_review_skip_missing_story_id", payload=payload)
        return
    if not isinstance(worktree_raw, str) or not worktree_raw:
        log.warning("security_review_skip_missing_worktree", payload=payload)
        return
    story_id = story_id_raw
    worktree = Path(worktree_raw)

    try:
        policy = load_security_review_policy(policy_path)
    except (PolicyNotFoundError, PolicyInvalidError) as e:
        log.warning("security_review_policy_load_failed", error=str(e))
        return

    if not policy.enabled:
        return

    frontmatter, story_text = _read_story_file(worktree, story_id)
    diff_text = await _git_diff_text(worktree)

    trigger = is_security_critical(
        story_id=story_id,
        frontmatter=frontmatter,
        story_text=story_text,
        diff_text=diff_text,
        policy=policy,
    )
    if trigger is None:
        log.info(
            "security_review_skipped_not_critical",
            story_id=story_id,
            worktree=str(worktree),
        )
        await bus.emit(
            EventType.SECURITY_REVIEW_PASSED,
            story_id=story_id,
            worktree=str(worktree),
            verdict=VERDICT_APPROVE,
            reason="not_security_critical",
        )
        return

    if runner is None:
        log.warning(
            "security_review_no_runner_configured",
            story_id=story_id,
            trigger=trigger.reason,
        )
        await bus.emit(
            EventType.SECURITY_REVIEW_PASSED,
            story_id=story_id,
            worktree=str(worktree),
            verdict=VERDICT_APPROVE,
            reason="no_runner_configured",
            trigger=trigger.reason,
        )
        return

    wave = str(payload.get("wave") or "default")
    try:
        verdict, findings_text = await runner(worktree, story_id, wave)
    except (OSError, RuntimeError) as exc:
        log.exception(
            "security_review_runner_failed",
            story_id=story_id,
            worktree=str(worktree),
        )
        verdict = VERDICT_ERROR
        findings_text = f"runner failed: {type(exc).__name__}: {exc}"

    if verdict not in ALL_VERDICTS:
        log.warning(
            "security_review_unrecognized_verdict",
            story_id=story_id,
            verdict=verdict,
        )
        verdict = VERDICT_ERROR

    log.info(
        "security_review_dispatched",
        story_id=story_id,
        worktree=str(worktree),
        trigger=trigger.reason,
        verdict=verdict,
    )

    if verdict in (VERDICT_APPROVE, VERDICT_MERGE_WITH_FIXES):
        await bus.emit(
            EventType.SECURITY_REVIEW_PASSED,
            story_id=story_id,
            worktree=str(worktree),
            verdict=verdict,
            trigger=trigger.reason,
            trigger_detail=trigger.detail,
            findings=findings_text,
        )
        return

    # BLOCK or ERROR → halt merge.
    halt_reason = (
        "security_review_block" if verdict == VERDICT_BLOCK else "security_review_error"
    )
    payload["verdict"] = "reject"
    gate_reasons_raw = payload.get("gate_reasons")
    gate_reasons: list[str]
    if isinstance(gate_reasons_raw, list):
        gate_reasons = [str(x) for x in gate_reasons_raw]
    else:
        gate_reasons = []
    gate_reasons.append(halt_reason)
    payload["gate_reasons"] = gate_reasons
    payload["security_review_verdict"] = verdict

    await bus.emit(
        EventType.HUMAN_QUERY,
        story_id=story_id,
        worktree=str(worktree),
        verdict=halt_reason,
        text=(
            f"{policy.escalation_text}\n\n"
            f"Story: {story_id}\n"
            f"Worktree: {worktree}\n"
            f"Trigger: {trigger.reason} ({trigger.detail})\n"
            f"Security verdict: {verdict}\n"
            f"--- findings ---\n{findings_text}"
        ),
        trigger=trigger.reason,
        security_verdict=verdict,
        actions=list(policy.block_actions),
    )


__all__ = [
    "ALL_VERDICTS",
    "SECURITY_REVIEW_POLICY_PATH_DEFAULT",
    "SECURITY_REVIEW_SKILL_INVOCATION",
    "VERDICT_APPROVE",
    "VERDICT_BLOCK",
    "VERDICT_ERROR",
    "VERDICT_MERGE_WITH_FIXES",
    "SecurityReviewPolicy",
    "SecurityReviewRunner",
    "SecurityTrigger",
    "extract_epic_from_story_id",
    "is_security_critical",
    "keyword_match_in_text",
    "load_security_review_policy",
    "parse_security_verdict",
    "parse_security_verdict_from_event",
    "security_review_subscriber",
]
