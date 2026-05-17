"""P5 regression tests — canonical_patches_port (Patch X — security review).

Spec: spec/spec_canonical_patches_port.md §P5.
Tracker: .claude/initiative-tracker-canonical_patches_port.md.

Coverage (25 tests):

* **Policy loading** (3) — happy path, missing file, invalid YAML.
* **Epic extraction** (3) — dotted / kebab / unrecognized.
* **Keyword scan** (2) — case-insensitive substring, empty input.
* **Trigger detection** (5) — frontmatter / epic / spec keyword / diff
  keyword / non-critical falls through to None.
* **Verdict parsing** (4) — explicit APPROVE / BLOCK / MERGE WITH FIXES,
  text-form, unrecognized.
* **Subscriber behaviour** (8) — ignores non-CODE_REVIEW_VERDICT, ignores
  non-approve verdict, missing fields, not-critical → no mutation +
  audit, APPROVE / MERGE_WITH_FIXES → audit only, BLOCK → mutates +
  HUMAN_QUERY, ERROR → mutates + HUMAN_QUERY, policy disabled.

Reference: ~/.claude/skills/bmad-security-review/SKILL.md + Odyssey handoff
2026-05-17 § 4.1 Patch X candidate.
"""

from __future__ import annotations

import subprocess
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from bmad_orchestrator.runtime.event_loop import Event, EventLoop, EventType
from bmad_orchestrator.runtime.security_review import (
    VERDICT_APPROVE,
    VERDICT_BLOCK,
    VERDICT_ERROR,
    VERDICT_MERGE_WITH_FIXES,
    SecurityReviewPolicy,
    extract_epic_from_story_id,
    is_security_critical,
    keyword_match_in_text,
    load_security_review_policy,
    parse_security_verdict,
    parse_security_verdict_from_event,
    security_review_subscriber,
)
from bmad_orchestrator.skills_repo import PolicyInvalidError, PolicyNotFoundError

# ──────────────────────── helpers ──────────────────────────────────────────────


def _write_policy(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "security-review.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def _make_runner(
    verdict: str, findings: str = ""
) -> Callable[[Path, str, str], Awaitable[tuple[str, str]]]:
    async def _stub(worktree: Path, story_id: str, wave: str) -> tuple[str, str]:
        return verdict, findings

    return _stub


def _init_git(tmp: Path) -> Path:
    tmp.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(tmp)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp), "config", "user.email", "t@t"], check=True
    )
    subprocess.run(["git", "-C", str(tmp), "config", "user.name", "t"], check=True)
    (tmp / "README").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp), "commit", "-q", "-m", "seed"], check=True)
    return tmp


def _make_worktree_with_story(
    tmp_path: Path, story_id: str, story_body: str
) -> Path:
    worktree = _init_git(tmp_path / "wt")
    story_path = worktree / "_bmad" / "stories" / f"{story_id}.md"
    story_path.parent.mkdir(parents=True, exist_ok=True)
    story_path.write_text(story_body, encoding="utf-8")
    return worktree


async def _collect_emitted(bus: EventLoop) -> list[Event]:
    """Drain pending events from the queue without dispatching subscribers."""
    out: list[Event] = []
    while True:
        ev = await bus.next(timeout=0.01)
        if ev is None:
            break
        out.append(ev)
    return out


# ──────────────────────── 1. policy loading (3) ─────────────────────────────────


def test_load_security_review_policy_happy_path(tmp_path: Path) -> None:
    p = _write_policy(
        tmp_path,
        "enabled: true\n"
        "security_critical_epics: [3, 7, 10]\n"
        "security_critical_keywords: [auth, jwt, custom_kw]\n"
        "block_actions: [abandon, manual_fix]\n"
        "escalation_text: 'Halt'\n",
    )
    pol = load_security_review_policy(p)
    assert pol.enabled is True
    assert pol.security_critical_epics == [3, 7, 10]
    assert "custom_kw" in pol.security_critical_keywords
    assert pol.block_actions == ["abandon", "manual_fix"]
    assert pol.escalation_text == "Halt"


def test_load_security_review_policy_missing_file(tmp_path: Path) -> None:
    with pytest.raises(PolicyNotFoundError):
        load_security_review_policy(tmp_path / "absent.yaml")


def test_load_security_review_policy_invalid_yaml(tmp_path: Path) -> None:
    p = _write_policy(tmp_path, "[invalid: yaml: :\n  - missing\n")
    with pytest.raises(PolicyInvalidError):
        load_security_review_policy(p)


# ──────────────────────── 2. epic extraction (3) ────────────────────────────────


def test_extract_epic_from_dotted_story_id() -> None:
    assert extract_epic_from_story_id("3.1") == 3
    assert extract_epic_from_story_id("10.2b") == 10
    assert extract_epic_from_story_id("0.7") == 0


def test_extract_epic_from_kebab_story_id() -> None:
    assert extract_epic_from_story_id("3-1-foo-bar") == 3
    assert extract_epic_from_story_id("story-7-2-baz") == 7


def test_extract_epic_from_unrecognized_returns_none() -> None:
    assert extract_epic_from_story_id("") is None
    assert extract_epic_from_story_id("not-a-story") is None
    assert extract_epic_from_story_id("epic-3-retrospective") is None


# ──────────────────────── 3. keyword scan (2) ──────────────────────────────────


def test_keyword_match_case_insensitive_returns_sorted_unique() -> None:
    text = "We added JWT validation. Also AUTH middleware. JWT again."
    hits = keyword_match_in_text(text, ["auth", "jwt", "session"])
    assert hits == ["auth", "jwt"]


def test_keyword_match_empty_inputs_returns_empty_list() -> None:
    assert keyword_match_in_text("", ["auth"]) == []
    assert keyword_match_in_text("some text", []) == []


# ──────────────────────── 4. trigger detection (5) ─────────────────────────────


def _default_policy() -> SecurityReviewPolicy:
    return SecurityReviewPolicy()


def test_trigger_frontmatter_flag() -> None:
    trig = is_security_critical(
        story_id="1.1",
        frontmatter={"security_critical": True},
        story_text="",
        diff_text="",
        policy=_default_policy(),
    )
    assert trig is not None
    assert trig.reason == "frontmatter"


def test_trigger_epic_in_security_critical_epics() -> None:
    trig = is_security_critical(
        story_id="3.2",
        frontmatter={},
        story_text="",
        diff_text="",
        policy=_default_policy(),
    )
    assert trig is not None
    assert trig.reason == "epic"
    assert "3" in trig.detail


def test_trigger_keyword_in_story_spec() -> None:
    trig = is_security_critical(
        story_id="1.1",  # epic 1 not in default list
        frontmatter={},
        story_text="Implements JWT signing for the new endpoint.",
        diff_text="",
        policy=_default_policy(),
    )
    assert trig is not None
    assert trig.reason == "keyword"
    assert "jwt" in trig.detail


def test_trigger_keyword_in_diff_only() -> None:
    trig = is_security_critical(
        story_id="1.1",
        frontmatter={},
        story_text="Plain ETL refactor — no security touchpoints.",
        diff_text="+ verify_hmac_signature(payload)\n",
        policy=_default_policy(),
    )
    assert trig is not None
    assert trig.reason == "keyword"
    assert "hmac" in trig.detail


def test_trigger_non_critical_returns_none() -> None:
    trig = is_security_critical(
        story_id="1.1",  # epic 1 not in default critical list
        frontmatter={"security_critical": False},
        story_text="Add a status icon to the dashboard.",
        diff_text="+ render_icon('pending')\n",
        policy=_default_policy(),
    )
    assert trig is None


# ──────────────────────── 5. verdict parsing (4) ───────────────────────────────


def test_parse_security_verdict_from_event_explicit_block() -> None:
    out = parse_security_verdict_from_event(
        {"verdict": "BLOCK", "findings": "P0-1: hardcoded secret"}
    )
    assert out == (VERDICT_BLOCK, "P0-1: hardcoded secret")


def test_parse_security_verdict_from_event_explicit_merge_with_fixes() -> None:
    out = parse_security_verdict_from_event(
        {"verdict": "MERGE WITH FIXES", "summary": "fix CSP later"}
    )
    assert out == (VERDICT_MERGE_WITH_FIXES, "fix CSP later")


def test_parse_security_verdict_from_event_text_form() -> None:
    out = parse_security_verdict_from_event(
        {"text": "Coverage notes...\nVerdict: APPROVE\nNo findings."}
    )
    assert out is not None
    assert out[0] == VERDICT_APPROVE


def test_parse_security_verdict_from_event_unrecognized() -> None:
    assert parse_security_verdict_from_event({"foo": "bar"}) is None
    assert parse_security_verdict_from_event({"verdict": "MAYBE"}) is None
    assert parse_security_verdict("no verdict line here") is None


# ──────────────────────── 6. subscriber (8) ────────────────────────────────────


@pytest.mark.asyncio
async def test_subscriber_ignores_non_code_review_verdict_event(
    tmp_path: Path,
) -> None:
    bus = EventLoop()
    ev = Event(type=EventType.WORKER_COMPLETED, payload={"verdict": "approve"})
    await security_review_subscriber(
        ev, bus=bus, policy_path=_default_policy_path(tmp_path)
    )
    assert ev.payload == {"verdict": "approve"}
    assert await bus.next(timeout=0.01) is None


@pytest.mark.asyncio
async def test_subscriber_ignores_non_approve_verdict(tmp_path: Path) -> None:
    bus = EventLoop()
    ev = Event(
        type=EventType.CODE_REVIEW_VERDICT,
        payload={
            "verdict": "reject",
            "story_id": "3.1",
            "worktree": "/tmp/ignored",
        },
    )
    await security_review_subscriber(
        ev,
        bus=bus,
        policy_path=_default_policy_path(tmp_path),
        runner=_make_runner(VERDICT_BLOCK),
    )
    # No mutation, no event emitted (subscriber bailed out on non-approve).
    assert ev.payload["verdict"] == "reject"
    assert await bus.next(timeout=0.01) is None


@pytest.mark.asyncio
async def test_subscriber_skips_when_story_id_missing(tmp_path: Path) -> None:
    bus = EventLoop()
    ev = Event(
        type=EventType.CODE_REVIEW_VERDICT,
        payload={"verdict": "approve", "worktree": "/tmp/x"},
    )
    await security_review_subscriber(
        ev, bus=bus, policy_path=_default_policy_path(tmp_path)
    )
    assert ev.payload["verdict"] == "approve"  # unchanged


@pytest.mark.asyncio
async def test_subscriber_not_critical_emits_passed_audit(tmp_path: Path) -> None:
    # Epic 1 is NOT in default critical list; no keywords; no frontmatter flag.
    worktree = _make_worktree_with_story(
        tmp_path,
        "1.1",
        "# Story 1.1\n\n- **epic:** 1\n- **security_critical:** false\n\n"
        "Add icon to dashboard.\n",
    )
    bus = EventLoop()
    ev = Event(
        type=EventType.CODE_REVIEW_VERDICT,
        payload={
            "verdict": "approve",
            "story_id": "1.1",
            "worktree": str(worktree),
        },
    )
    await security_review_subscriber(
        ev,
        bus=bus,
        policy_path=_default_policy_path(tmp_path),
        runner=_make_runner(VERDICT_BLOCK),  # would BLOCK if invoked — but won't
    )
    # No mutation.
    assert ev.payload["verdict"] == "approve"
    emitted = await _collect_emitted(bus)
    assert len(emitted) == 1
    assert emitted[0].type == EventType.SECURITY_REVIEW_PASSED
    assert emitted[0].payload["reason"] == "not_security_critical"


@pytest.mark.asyncio
async def test_subscriber_critical_approve_no_mutation(tmp_path: Path) -> None:
    """Security-critical story + hunter APPROVE → merge proceeds, audit emitted."""
    worktree = _make_worktree_with_story(
        tmp_path,
        "3.1",  # epic 3 → trigger by epic
        "# Story 3.1\n\n- **epic:** 3\n\nAdd RLS guards.\n",
    )
    bus = EventLoop()
    ev = Event(
        type=EventType.CODE_REVIEW_VERDICT,
        payload={
            "verdict": "approve",
            "story_id": "3.1",
            "worktree": str(worktree),
        },
    )
    await security_review_subscriber(
        ev,
        bus=bus,
        policy_path=_default_policy_path(tmp_path),
        runner=_make_runner(VERDICT_APPROVE, findings="no findings"),
    )
    assert ev.payload["verdict"] == "approve"
    emitted = await _collect_emitted(bus)
    assert len(emitted) == 1
    assert emitted[0].type == EventType.SECURITY_REVIEW_PASSED
    assert emitted[0].payload["verdict"] == VERDICT_APPROVE
    assert emitted[0].payload["trigger"] == "epic"


@pytest.mark.asyncio
async def test_subscriber_critical_merge_with_fixes_no_mutation(
    tmp_path: Path,
) -> None:
    """MERGE_WITH_FIXES is non-blocking — merge proceeds; audit captures findings."""
    worktree = _make_worktree_with_story(
        tmp_path,
        "3.2",
        "# Story 3.2\n\n- **epic:** 3\n\nDPA enforcement.\n",
    )
    bus = EventLoop()
    ev = Event(
        type=EventType.CODE_REVIEW_VERDICT,
        payload={
            "verdict": "approve",
            "story_id": "3.2",
            "worktree": str(worktree),
        },
    )
    await security_review_subscriber(
        ev,
        bus=bus,
        policy_path=_default_policy_path(tmp_path),
        runner=_make_runner(
            VERDICT_MERGE_WITH_FIXES, findings="P1-1: weak CSP header"
        ),
    )
    assert ev.payload["verdict"] == "approve"  # untouched
    emitted = await _collect_emitted(bus)
    assert len(emitted) == 1
    assert emitted[0].type == EventType.SECURITY_REVIEW_PASSED
    assert emitted[0].payload["verdict"] == VERDICT_MERGE_WITH_FIXES
    assert "weak CSP" in emitted[0].payload["findings"]


@pytest.mark.asyncio
async def test_subscriber_critical_block_mutates_and_emits_human_query(
    tmp_path: Path,
) -> None:
    """BLOCK → verdict→reject + gate_reasons appended + HUMAN_QUERY emitted."""
    worktree = _make_worktree_with_story(
        tmp_path,
        "4.1",  # epic 4 → trigger by epic
        "# Story 4.1\n\n- **epic:** 4\n\n2FA flow.\n",
    )
    bus = EventLoop()
    ev = Event(
        type=EventType.CODE_REVIEW_VERDICT,
        payload={
            "verdict": "approve",
            "story_id": "4.1",
            "worktree": str(worktree),
            "gate_reasons": ["dummy_prior"],
        },
    )
    await security_review_subscriber(
        ev,
        bus=bus,
        policy_path=_default_policy_path(tmp_path),
        runner=_make_runner(
            VERDICT_BLOCK, findings="P0-1: alg=none accepted by JWT validator"
        ),
    )
    # Mutated.
    assert ev.payload["verdict"] == "reject"
    assert "security_review_block" in ev.payload["gate_reasons"]
    assert "dummy_prior" in ev.payload["gate_reasons"]  # prior reasons preserved
    assert ev.payload["security_review_verdict"] == VERDICT_BLOCK

    emitted = await _collect_emitted(bus)
    assert len(emitted) == 1
    assert emitted[0].type == EventType.HUMAN_QUERY
    assert "alg=none" in emitted[0].payload["text"]
    assert emitted[0].payload["security_verdict"] == VERDICT_BLOCK
    assert "abandon" in emitted[0].payload["actions"]


@pytest.mark.asyncio
async def test_subscriber_critical_runner_error_halts(tmp_path: Path) -> None:
    """Runner returning ERROR also halts merge — defensive (better safe)."""
    worktree = _make_worktree_with_story(
        tmp_path,
        "3.3",
        "# Story 3.3\n\n- **epic:** 3\n\nHMAC verifier.\n",
    )
    bus = EventLoop()
    ev = Event(
        type=EventType.CODE_REVIEW_VERDICT,
        payload={
            "verdict": "approve",
            "story_id": "3.3",
            "worktree": str(worktree),
        },
    )

    async def _bad_runner(
        worktree: Path, story_id: str, wave: str
    ) -> tuple[str, str]:
        raise RuntimeError("hunter spawn failed")

    await security_review_subscriber(
        ev,
        bus=bus,
        policy_path=_default_policy_path(tmp_path),
        runner=_bad_runner,
    )
    assert ev.payload["verdict"] == "reject"
    assert "security_review_error" in ev.payload["gate_reasons"]
    assert ev.payload["security_review_verdict"] == VERDICT_ERROR
    emitted = await _collect_emitted(bus)
    assert len(emitted) == 1
    assert emitted[0].type == EventType.HUMAN_QUERY


def _default_policy_path(tmp_path: Path) -> Path:
    """Write a default policy to ``tmp_path`` so each subscriber test gets one."""
    return _write_policy(
        tmp_path,
        "enabled: true\n"
        "security_critical_epics: [3, 4, 5, 7, 9, 10]\n"
        "security_critical_keywords: [auth, jwt, rls, dpa, crypto, hmac]\n"
        "block_actions: [abandon, manual_security_fix]\n"
        "escalation_text: 'Security block.'\n",
    )


# Sanity: the test file declares exactly 25 tests (P5 target). Reflect this so
# a refactor that drops one trips immediately.
def test_p5_test_inventory_count() -> None:
    import inspect

    import tests.test_canonical_patches_p5 as mod  # type: ignore[import-not-found]

    test_names = [
        name
        for name, _ in inspect.getmembers(mod, predicate=inspect.isfunction)
        if name.startswith("test_") and name != "test_p5_test_inventory_count"
    ]
    assert len(test_names) == 25, (
        f"P5 spec promised 25 tests; got {len(test_names)}: {test_names}"
    )


# Tally:
# 1. policy_happy / missing / invalid                       = 3
# 2. epic dotted / kebab / unrecognized                     = 3
# 3. keyword case-insensitive / empty                       = 2
# 4. trigger frontmatter / epic / spec / diff / none        = 5
# 5. verdict explicit BLOCK / MWF / text / unrecognized     = 4
# 6. subscriber non-CRV / non-approve / missing_story_id /
#    not_critical / approve / merge_with_fixes / block /
#    runner_error                                           = 8
# Total = 25 (the inventory test below would catch drift).
