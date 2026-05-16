"""FS5 round 2 PoC tests for C5 (atomic budget) and C6 (Telegram secret scrub).

* **C5 — atomic enforce_and_reserve**: 10 concurrent ``asyncio.gather`` workers,
  each attempts to reserve $5 against a story budget where ``halt=$50`` and the
  pre-existing spend is $48. Only ONE worker can fit ($48 + $5 = $53 > $50, but
  $48 + $5 = $53 over halt → none should fit? Spec says one fits because the
  reservation atomically updates spent_usd, the next concurrent caller observes
  the updated row through BEGIN IMMEDIATE). See the test docstring for the
  precise contract.

* **C6 — Telegram outbound secret scrub**: bare ``scrub_output`` must redact API
  keys (sk-ant-…), GitHub PATs (ghp_…), Telegram bot tokens (123:AAA…), URL
  credentials, AWS access keys (AKIA…) so a leaked agent error message can never
  carry a live secret to a Telegram chat.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from pathlib import Path

import pytest

from bmad_orchestrator.agent.safety.budget_guard import BudgetGuard
from bmad_orchestrator.bot.pii_detector import scrub_output
from bmad_orchestrator.config import BudgetConfig
from bmad_orchestrator.state.db import StateDB

# ─────────────────────────────────────────────────────────────────────────────
# C5 — atomic enforce_and_reserve
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_c5_concurrent_reserve_only_one_fits(tmp_path: Path) -> None:
    """10 ``gather``-fired workers each ask for $5. Pre-existing spend=$48,
    halt=$50. Atomic contract: ONLY workers whose ``current + reserve ≤ halt``
    win. With spent_usd=$48 and reserve=$5 → projected=$53 > $50 for everyone
    after the first reservation — so exactly ONE fits if alarm threshold is
    relaxed and the cap check uses ``≤``; with strict ``>`` semantics ZERO
    should fit. The chosen semantics (round 2 spec line 98): ``if current +
    reserve > halt → ROLLBACK``, so $48 + $5 = $53 > $50 → no winner.

    The PoC in the spec assumes a slightly looser contract — *one* worker wins
    because the first reservation lands when current=$48 and is allowed only
    if the test fixture pre-loaded $48 such that exactly one $5 reserve fits.
    We use halt=$53 (not $50) here so the math is unambiguous: $48 + $5 = $53
    EXACTLY → one wins, then $53 + $5 = $58 > $53 → nine lose. This preserves
    the spirit of the test (concurrent serialisation) without ambiguous
    boundary arithmetic.
    """
    db_path = tmp_path / "state.db"
    db = StateDB(db_path=db_path)
    await db.init()
    session_id = await db.create_session(target_project="x", wave="w", max_parallel=10)

    # Pre-load $48 spent via the legacy upsert path.
    await db.upsert_budget(
        session_id=session_id,
        scope="story",
        scope_target_id="S1",
        spent_usd=48.0,
        spent_tokens=0,
        alarm_threshold=30.0,
        halt_threshold=53.0,
    )

    cfg = BudgetConfig(
        story_alarm_usd=30.0,
        story_halt_usd=53.0,
        batch_alarm_usd=200.0,
        batch_halt_usd=300.0,
        daily_limit_usd=500.0,
    )
    guard = BudgetGuard(cfg, state_db=db, session_id=session_id)

    async def worker() -> str:
        res = await guard.enforce_and_reserve_story("S1", 5.0)
        return res.reason

    results = await asyncio.gather(*[worker() for _ in range(10)])
    counts = Counter(results)

    # Atomic contract — exactly one winner.
    assert counts["ok"] == 1, (
        f"expected exactly 1 ok, got {counts['ok']} (race detected). "
        f"breakdown: {dict(counts)}"
    )
    assert counts["halt_breached"] == 9, (
        f"expected 9 halt_breached, got {counts['halt_breached']}. "
        f"breakdown: {dict(counts)}"
    )

    # Final DB state — $48 (pre-existing) + $5 (single winner) = $53.
    row = await db.get_budget(session_id, "story", "S1")
    assert row is not None
    assert row["spent_usd"] == pytest.approx(53.0), (
        f"final spent_usd should be 53.0, got {row['spent_usd']} — multiple "
        f"writers overcommitted"
    )


@pytest.mark.asyncio
async def test_c5_unbound_guard_fallback(tmp_path: Path) -> None:
    """When ``state_db`` is not attached, ``enforce_and_reserve_story`` returns
    a deterministic synthetic result instead of crashing — used by unit tests
    and the pre-pilot stages of CI that don't construct a StateDB."""
    cfg = BudgetConfig(
        story_alarm_usd=30.0,
        story_halt_usd=50.0,
        batch_alarm_usd=200.0,
        batch_halt_usd=300.0,
        daily_limit_usd=500.0,
    )
    guard = BudgetGuard(cfg)  # no state_db

    res = await guard.enforce_and_reserve_story("S1", 5.0)
    assert res.allowed is True
    assert res.reason == "ok"

    over = await guard.enforce_and_reserve_story("S1", 999.0)
    assert over.allowed is False
    assert over.reason == "halt_breached"


@pytest.mark.asyncio
async def test_c5_corruption_safe_halt(tmp_path: Path) -> None:
    """NaN / inf / negative reserve amounts must NOT bypass the cap. Round 1
    accidentally let negative numbers slip through because the ``< halt``
    check passed when current=$48 and reserve=$-100. ``is_finite_spend``
    rejects them upstream and returns a synthetic deny."""
    import math

    db_path = tmp_path / "state.db"
    db = StateDB(db_path=db_path)
    await db.init()
    session_id = await db.create_session(target_project="x", wave="w", max_parallel=2)

    cfg = BudgetConfig(
        story_alarm_usd=30.0,
        story_halt_usd=50.0,
        batch_alarm_usd=200.0,
        batch_halt_usd=300.0,
        daily_limit_usd=500.0,
    )
    guard = BudgetGuard(cfg, state_db=db, session_id=session_id)

    for bad in (math.nan, math.inf, -math.inf, -100.0):
        res = await guard.enforce_and_reserve_story("S1", bad)
        assert res.allowed is False, f"corrupted reserve {bad!r} bypassed cap"
        assert res.reason == "corruption", (
            f"corrupted reserve {bad!r} did not surface as 'corruption', "
            f"got {res.reason!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# C6 — Telegram outbound secret scrub
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,must_not_contain",
    [
        # Anthropic API key — sk-ant-…
        (
            "ошибка: 401 Bearer sk-ant-AAAA1234567890ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef",
            "sk-ant-",
        ),
        # Telegram bot token — DIGITS:35-alnum
        (
            "выдан токен 123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            ":AAA",
        ),
        # GitHub Personal Access Token (classic)
        (
            "auth header: ghp_1234567890abcdef1234567890abcdef1234",
            "ghp_",
        ),
        # GitHub Fine-grained PAT (must be exactly 82 chars after the prefix)
        (
            "auth: github_pat_11ABCDEFG0123456789012_"
            "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJKLMNOPQRSTUVW",
            "github_pat_",
        ),
        # AWS Access Key
        ("AWS key AKIAIOSFODNN7EXAMPLE", "AKIA"),
        # URL with embedded credentials
        (
            "git clone https://user:secretpass@github.com/x/y.git",
            "secretpass",
        ),
        # Generic Bearer
        (
            "Authorization: Bearer abcDEF123456789012345678901234567890",
            "abcDEF12345",
        ),
    ],
)
def test_c6_scrub_output_removes_secret(raw: str, must_not_contain: str) -> None:
    """Every API-key / token pattern listed in ``secret_patterns.SECRET_PATTERNS``
    must be stripped from ``scrub_output`` BEFORE PII redaction runs (so a
    leaked secret never reaches Telegram even when PII regexes don't match it)."""
    scrubbed, categories = scrub_output(raw)
    assert must_not_contain not in scrubbed, (
        f"SECRET LEAK: scrub_output kept {must_not_contain!r} in output:\n"
        f"  raw:      {raw!r}\n"
        f"  scrubbed: {scrubbed!r}"
    )
    assert categories, (
        f"scrub_output returned empty categories for raw={raw!r} — should have "
        f"recorded the redaction kind"
    )


def test_c6_multiple_secrets_in_one_message() -> None:
    """Composite case — agent error message carrying several secret kinds at
    once. ALL must be redacted, NONE must leak."""
    raw = (
        "POST https://user:hunter2@github.com/api/x "
        "Authorization: Bearer sk-ant-AAAA1234567890ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef "
        "X-GH-Token: ghp_1234567890abcdef1234567890abcdef1234 "
        "X-AWS: AKIAIOSFODNN7EXAMPLE "
        "X-TG: 123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    )
    scrubbed, categories = scrub_output(raw)
    for fragment in (
        "sk-ant-",
        "ghp_",
        "AKIA",
        "hunter2",
        ":AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    ):
        assert fragment not in scrubbed, (
            f"SECRET LEAK: {fragment!r} still present in scrubbed output:\n"
            f"  {scrubbed!r}"
        )
    # We expect 4 or 5 distinct categories surfaced (the URL creds may be
    # caught by either URL_CREDS or BEARER depending on order; both are OK).
    assert len(categories) >= 4
