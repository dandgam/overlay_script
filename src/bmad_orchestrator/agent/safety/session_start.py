"""SessionStart hook — force-load worker policy into env before subprocess.

Spec: spec_phase4_hardening §1.1.

Injects a bootstrap block into the worker environment via
``ORCHESTRATOR_SESSION_BOOTSTRAP`` so the worker session starts with:
  - The skill's SKILL.md first paragraph (≤10 lines, truncated)
  - The current BMad workflow phase marker
  - The worker session policy excerpt (4 sections)
  - Optional: resumed-state summary from PreCompact persistence (item #2)

The block is plain text — the worker process can read it from the env-var
on startup to restore full context without relying on CLAUDE.md drift.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

# Canonical skill slugs shipped with the orchestrator.
CANONICAL_SKILLS: frozenset[str] = frozenset({
    "bmad-dev-story",
    "bmad-code-review",
    "bmad-investigate",
    "bmad-correct-course",
    "bmad-sprint-planning",
    "bmad-auto-dev",
    "bmad-create-story",
    "bmad-quick-dev",
    "bmad-retrospective",
    "bmad-sprint-status",
    "bmad-checkpoint-preview",
    "bmad-review-adversarial-general",
    "bmad-review-edge-case-hunter",
    "bmad-advanced-elicitation",
    "bmad-agent-dev",
    "bmad-customize",
})

# The env-var key that workers should read on startup.
SESSION_BOOTSTRAP_ENV_KEY = "ORCHESTRATOR_SESSION_BOOTSTRAP"

# Path to the worker session policy document, relative to this file's package root.
_POLICY_PATH = Path(__file__).parent / "policies" / "worker_session_policy.md"

# BMad Phase 4 workflow phase marker embedded in every bootstrap.
_WORKFLOW_PHASE_MARKER = "BMad Phase 4 (Implementation) — story execution pipeline active"


def _read_policy_excerpt() -> str:
    """Read the worker_session_policy.md and return a condensed excerpt.

    Returns the full content trimmed to the first 3000 characters to keep
    the env-var reasonably sized while preserving all 4 policy sections.
    If the file is missing or unreadable, returns a minimal inline fallback.
    """
    try:
        text = _POLICY_PATH.read_text(encoding="utf-8")
        # Trim to first 3000 chars — covers all 4 sections on any reasonable policy doc.
        return text[:3000].strip()
    except OSError:
        return (
            "WORKER POLICY (fallback — policy file unreadable):\n"
            "1. NO MERGE WITHOUT GATE PASS\n"
            "2. 3-ATTEMPT CAP — ESCALATE\n"
            "3. EVIDENCE-BASED COMPLETION CLAIMS (no 'done!', 'should work', etc.)\n"
            "4. SANDBOX BOUNDARIES (no .env reads, no curl|bash)\n"
        )


def _read_skill_snippet(skill_slug: str) -> str:
    """Read the first paragraph (≤10 lines) of the skill's SKILL.md.

    Searches the orchestrator's ``skills/upstream/<slug>/SKILL.md`` path.
    Returns a fallback line if the file is not found.
    """
    # The skills/ directory is two levels up from agent/safety/
    # src/bmad_orchestrator/agent/safety/ → src/bmad_orchestrator/ → src/ → project root → skills/
    package_root = Path(__file__).parent.parent.parent.parent.parent
    skill_path = package_root / "skills" / "upstream" / skill_slug / "SKILL.md"

    if not skill_path.exists():
        return f"[SKILL.md not found for {skill_slug!r}]"

    try:
        lines = skill_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return f"[SKILL.md unreadable for {skill_slug!r}]"

    # Take first non-empty paragraph (up to 10 lines).
    collected: list[str] = []
    blank_count = 0
    for line in lines:
        if not line.strip():
            blank_count += 1
            if blank_count >= 2 and collected:
                # End of first paragraph.
                break
        else:
            blank_count = 0
            collected.append(line)
        if len(collected) >= 10:
            break

    return "\n".join(collected) if collected else f"[SKILL.md empty for {skill_slug!r}]"


def build_session_start_block(
    skill_slug: str,
    story_id: str,
    resumed_state: dict[str, object] | None = None,
) -> str:
    """Build the SessionStart additionalContext block.

    Args:
        skill_slug: The skill being run (e.g. 'bmad-dev-story').
        story_id: The story identifier being processed.
        resumed_state: Optional dict from ``MemoryPersistor.load_state``.
            When present, a 'Resumed from:' line is prepended to the block.

    Returns:
        A plain-text string suitable for ``ORCHESTRATOR_SESSION_BOOTSTRAP``.
    """
    skill_snippet = _read_skill_snippet(skill_slug)
    policy_excerpt = _read_policy_excerpt()

    parts: list[str] = [
        "=== Worker session bootstrap ===",
        f"Skill: {skill_slug}",
        f"Story: {story_id}",
        f"Phase: {_WORKFLOW_PHASE_MARKER}",
        "",
    ]

    # Embed resumed state if available (item #2 integration).
    if resumed_state:
        retry = resumed_state.get("retry_count", 0)
        drift = resumed_state.get("scope_drift_warnings", 0)
        last_skill = resumed_state.get("active_skill", skill_slug)
        parts.append(f"Resumed from: retry={retry}, scope_drift={drift}, last skill={last_skill}")
        parts.append("")

    parts += [
        "--- Skill overview ---",
        skill_snippet,
        "",
        "--- Worker policy ---",
        policy_excerpt,
        "",
        "=== End bootstrap ===",
    ]

    return "\n".join(parts)


def inject_into_worker_env(
    env: dict[str, str],
    block: str,
) -> dict[str, str]:
    """Inject the bootstrap block into a worker environment dict.

    Does NOT mutate the input dict. Idempotent: if the key already exists,
    it is replaced with the new block (not appended).

    Args:
        env: The base worker environment dict (output of ``_build_worker_env``).
        block: The bootstrap block from ``build_session_start_block``.

    Returns:
        A new dict with ``SESSION_BOOTSTRAP_ENV_KEY`` set to ``block``.
    """
    new_env = dict(env)
    new_env[SESSION_BOOTSTRAP_ENV_KEY] = block
    return new_env


def _resolve_skill_slug(payload: Mapping[str, object]) -> str:
    """Extract skill slug from a spawn payload dict.

    Checks ``skill_slug`` → ``skill_invocation`` (extract first word after
    known prefixes) → falls back to ``bmad-auto-dev`` (the default worker skill).
    """
    # Direct key (set by callers that know the skill explicitly).
    slug = payload.get("skill_slug")
    if isinstance(slug, str) and slug:
        return slug

    # Derive from skill_invocation string.
    invocation = payload.get("skill_invocation", "")
    if isinstance(invocation, str) and invocation:
        for known in CANONICAL_SKILLS:
            if known in invocation:
                return known

    # Default.
    return "bmad-auto-dev"


__all__ = [
    "CANONICAL_SKILLS",
    "SESSION_BOOTSTRAP_ENV_KEY",
    "_resolve_skill_slug",
    "build_session_start_block",
    "inject_into_worker_env",
]
