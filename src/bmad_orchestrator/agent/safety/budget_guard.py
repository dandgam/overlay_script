"""Deterministic budget interceptor (spec §9 layer 2 + §8).

Three-tier caps:
- Per story: $30 alarm, $50 halt
- Per batch: $200 alarm, $300 halt
- Per day:   $500 limit (single hard cap; alarm == halt)

`BudgetGuard.enforce_story` / `enforce_batch` / `enforce_day` возвращают
`BudgetResult` с уровнем (`ok` | `alarm` | `halt`). На уровне `halt` они
дополнительно эмитят `budget_threshold_hit` event в подключённый `EventLoop`.

FS3 hardening (B5):
- NaN / inf / negative `spent_usd` → emit `budget_corruption` audit critical
  and return synthetic `halt` (safe-default — caller MUST stop spawn).
- `enforce_*` calls perform a transactional read-and-update in a single
  BEGIN IMMEDIATE block via `enforce_and_reserve` (when a StateDB binding is
  attached) so concurrent worker spawns cannot bypass the cap by racing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Literal

from bmad_orchestrator.agent.safety.audit import record_audit
from bmad_orchestrator.config import BudgetConfig
from bmad_orchestrator.models import Budget
from bmad_orchestrator.runtime.budget import is_finite_spend
from bmad_orchestrator.runtime.event_loop import EventLoop, EventType

if TYPE_CHECKING:
    from bmad_orchestrator.state.db import BudgetEnforceResult, StateDB

BudgetLevel = Literal["ok", "alarm", "halt"]
BudgetScope = Literal["story", "batch", "day"]


@dataclass(slots=True)
class BudgetResult:
    scope: BudgetScope
    spent_usd: float
    level: BudgetLevel
    alarm_threshold: float
    halt_threshold: float
    breached_alarm: bool
    breached_halt: bool
    corrupted: bool = False

    def as_budget(self) -> Budget:
        # Budget Pydantic model accepts story|batch|wave|day|phase — narrow our
        # day/story/batch into matching values.
        return Budget(
            scope=self.scope,
            spent_usd=self.spent_usd,
            spent_tokens=0,
            alarm_threshold=self.alarm_threshold,
            halt_threshold=self.halt_threshold,
            breached_alarm=self.breached_alarm,
            breached_halt=self.breached_halt,
        )


def _level(spent: float, alarm: float, halt: float) -> BudgetLevel:
    if spent >= halt:
        return "halt"
    if spent >= alarm:
        return "alarm"
    return "ok"


def _corruption_result(
    spent_usd: float, alarm: float, halt: float, scope: BudgetScope
) -> BudgetResult:
    return BudgetResult(
        scope=scope,
        spent_usd=spent_usd if isinstance(spent_usd, (int, float)) else 0.0,
        level="halt",
        alarm_threshold=alarm,
        halt_threshold=halt,
        breached_alarm=True,
        breached_halt=True,
        corrupted=True,
    )


class BudgetGuard:
    """Pluggable budget enforcer.

    Не хранит состояние сама — caller передаёт `spent_usd` агрегированный из state.db.
    Это нужно чтобы guard оставался deterministic (нет skew из-за внутреннего кеша).
    """

    def __init__(
        self,
        cfg: BudgetConfig,
        event_loop: EventLoop | None = None,
        state_db: StateDB | None = None,
        session_id: int | None = None,
    ) -> None:
        self.cfg = cfg
        self.event_loop = event_loop
        # Optional binding: when both ``state_db`` and ``session_id`` are set,
        # ``enforce_and_reserve_story|batch|day`` delegate to the atomic
        # check-and-reserve in ``StateDB.enforce_and_reserve`` (C5 round 2).
        # If unbound, the methods fall back to the synchronous deterministic
        # ``_evaluate`` (unit tests and pre-pilot stages).
        self.state_db = state_db
        self.session_id = session_id
        # W2 / W3 — non-tiered attribution of ad-hoc LLM spend (intent_router,
        # worker:<story>, …) toward the daily cap. In-memory aggregate only;
        # StateDB still owns the durable day rollup, but ``attribute_usd``
        # surfaces an immediate halt signal so dispatch can short-circuit
        # before issuing the next API call.
        self._attributed_per_scope: dict[str, Decimal] = {}
        self._attributed_total: Decimal = Decimal("0")

    def attach_state_db(self, state_db: StateDB, session_id: int) -> None:
        """Late-binding helper — wire the guard to a shared StateDB after init."""
        self.state_db = state_db
        self.session_id = session_id

    # ── public API ─────────────────────────────────────────────────────────────

    async def enforce_story(self, spent_usd: float, story_id: str) -> BudgetResult:
        result = self._evaluate(
            spent_usd, self.cfg.story_alarm_usd, self.cfg.story_halt_usd, scope="story"
        )
        await self._publish(result, story_id=story_id)
        return result

    async def enforce_batch(self, spent_usd: float, wave: str) -> BudgetResult:
        result = self._evaluate(
            spent_usd, self.cfg.batch_alarm_usd, self.cfg.batch_halt_usd, scope="batch"
        )
        await self._publish(result, wave=wave)
        return result

    async def enforce_day(self, spent_usd: float, day: str) -> BudgetResult:
        """Daily aggregate cap (spec §8). Single threshold — alarm == halt.

        Caller (orchestrator main loop) is responsible for computing
        `spent_today_usd` from state.db.budget_tracker rows for scope='day'.
        """
        limit = self.cfg.daily_limit_usd
        result = self._evaluate(spent_usd, limit, limit, scope="day")
        await self._publish(result, day=day)
        return result

    # ── atomic reserve API (C5 round 2 — REAL impl, not paper) ────────────────

    async def enforce_and_reserve_story(
        self, story_id: str, reserve_usd: Decimal | float
    ) -> BudgetEnforceResult:
        """Atomically reserve ``reserve_usd`` against the story budget.

        Returns ``allowed=True`` only when ``current + reserve ≤ story_halt``.
        Concurrent gather workers serialise on the SQLite write lock — exactly
        one can fit the last slot under the cap, others get ``halt_breached``.
        """
        return await self._enforce_and_reserve(
            scope="story",
            scope_target_id=story_id,
            reserve_usd=reserve_usd,
            alarm=self.cfg.story_alarm_usd,
            halt=self.cfg.story_halt_usd,
        )

    async def enforce_and_reserve_batch(
        self, wave: str, reserve_usd: Decimal | float
    ) -> BudgetEnforceResult:
        return await self._enforce_and_reserve(
            scope="batch",
            scope_target_id=wave,
            reserve_usd=reserve_usd,
            alarm=self.cfg.batch_alarm_usd,
            halt=self.cfg.batch_halt_usd,
        )

    async def enforce_and_reserve_day(
        self, day: str, reserve_usd: Decimal | float
    ) -> BudgetEnforceResult:
        limit = self.cfg.daily_limit_usd
        return await self._enforce_and_reserve(
            scope="day",
            scope_target_id=day,
            reserve_usd=reserve_usd,
            alarm=limit,
            halt=limit,
        )

    async def _enforce_and_reserve(
        self,
        *,
        scope: str,
        scope_target_id: str,
        reserve_usd: Decimal | float,
        alarm: float,
        halt: float,
    ) -> BudgetEnforceResult:
        """Internal — delegate to StateDB if bound, otherwise synthetic result."""
        from bmad_orchestrator.state.db import BudgetEnforceResult

        if self.state_db is None or self.session_id is None:
            # Unbound mode — synchronous evaluation only. Surface "allowed"
            # against the legacy ``_evaluate`` path so callers that never
            # attached a DB still get a deterministic decision.
            reserve_float = float(reserve_usd)
            if not is_finite_spend(reserve_float):
                return BudgetEnforceResult(
                    allowed=False,
                    scope=scope,
                    scope_target_id=scope_target_id,
                    current_usd=0.0,
                    attempted=0.0,
                    alarm_threshold=alarm,
                    halt_threshold=halt,
                    reason="corruption",
                    breached_alarm=True,
                    breached_halt=True,
                )
            allowed = reserve_float <= halt
            return BudgetEnforceResult(
                allowed=allowed,
                scope=scope,
                scope_target_id=scope_target_id,
                current_usd=reserve_float if allowed else 0.0,
                attempted=reserve_float,
                alarm_threshold=alarm,
                halt_threshold=halt,
                reason="ok" if allowed else "halt_breached",
                breached_alarm=reserve_float >= alarm,
                breached_halt=reserve_float >= halt,
            )

        result = await self.state_db.enforce_and_reserve(
            session_id=self.session_id,
            scope=scope,
            scope_target_id=scope_target_id,
            reserve_usd=reserve_usd,
            alarm_threshold=alarm,
            halt_threshold=halt,
        )
        if not result.allowed and result.reason == "halt_breached":
            record_audit(
                "budget_halt_breached",
                scope=scope,
                scope_target_id=scope_target_id,
                current_usd=result.current_usd,
                attempted=result.attempted,
                halt_threshold=halt,
            )
            if self.event_loop is not None:
                await self.event_loop.emit(
                    EventType.BUDGET_THRESHOLD_HIT,
                    scope=scope,
                    level="halt",
                    spent_usd=result.current_usd,
                    alarm_threshold=alarm,
                    halt_threshold=halt,
                    corrupted=False,
                )
        return result

    # ── ad-hoc attribution (W2/W3 — intent_router, worker JSONL cost) ─────────

    async def attribute_usd(
        self, *, scope: str, spent: float | Decimal
    ) -> BudgetResult:
        """Attribute a non-tiered LLM spend toward the day cap.

        Used by W2 (``scope="intent_router"``) and W3 (``scope=f"worker:{story_id}"``)
        to bridge per-call usage into the day-level budget. Maintains an
        in-memory cumulative total so dispatch can short-circuit before the
        next API call when the daily limit is breached. Returns a
        ``BudgetResult`` whose ``level`` reflects the day cap after this
        attribution. NaN / inf / negative → safe-default ``halt`` + audit
        critical (mirrors ``_evaluate`` behaviour).
        """
        if not is_finite_spend(spent):
            record_audit(
                "budget_corruption",
                scope=scope,
                spent_usd_repr=repr(spent),
                origin="attribute_usd",
                severity="critical",
            )
            return _corruption_result(
                float(spent) if isinstance(spent, (int, float)) else 0.0,
                self.cfg.daily_limit_usd,
                self.cfg.daily_limit_usd,
                "day",
            )
        delta = Decimal(str(spent))
        self._attributed_per_scope[scope] = (
            self._attributed_per_scope.get(scope, Decimal("0")) + delta
        )
        self._attributed_total += delta
        result = self._evaluate(
            self._attributed_total,
            self.cfg.daily_limit_usd,
            self.cfg.daily_limit_usd,
            scope="day",
        )
        await self._publish(result, attribution_scope=scope)
        return result

    def attributed_total(self) -> Decimal:
        """Snapshot — cumulative ``attribute_usd`` spend across all scopes."""
        return self._attributed_total

    def attributed_for(self, scope: str) -> Decimal:
        """Snapshot — cumulative ``attribute_usd`` spend for one scope."""
        return self._attributed_per_scope.get(scope, Decimal("0"))

    # Sync probes для unit-тестов / TUI dashboard.
    def check_story(self, spent_usd: float) -> BudgetResult:
        return self._evaluate(
            spent_usd, self.cfg.story_alarm_usd, self.cfg.story_halt_usd, scope="story"
        )

    def check_batch(self, spent_usd: float) -> BudgetResult:
        return self._evaluate(
            spent_usd, self.cfg.batch_alarm_usd, self.cfg.batch_halt_usd, scope="batch"
        )

    def check_day(self, spent_usd: float) -> BudgetResult:
        limit = self.cfg.daily_limit_usd
        return self._evaluate(spent_usd, limit, limit, scope="day")

    # ── internals ──────────────────────────────────────────────────────────────

    @staticmethod
    def _evaluate(
        spent_usd: float | Decimal,
        alarm: float,
        halt: float,
        *,
        scope: BudgetScope,
    ) -> BudgetResult:
        # B5 NaN/inf/negative guard — fail safe (halt) rather than fail open.
        if not is_finite_spend(spent_usd):
            record_audit(
                "budget_corruption",
                scope=scope,
                spent_usd_repr=repr(spent_usd),
                alarm_threshold=alarm,
                halt_threshold=halt,
                severity="critical",
            )
            return _corruption_result(
                spent_usd if isinstance(spent_usd, (int, float)) else 0.0,
                alarm,
                halt,
                scope,
            )
        spent_float = float(spent_usd)
        if math.isnan(spent_float) or not math.isfinite(spent_float):  # belt-and-braces
            record_audit(
                "budget_corruption",
                scope=scope,
                spent_usd_repr=repr(spent_usd),
                alarm_threshold=alarm,
                halt_threshold=halt,
                severity="critical",
            )
            return _corruption_result(spent_float, alarm, halt, scope)

        lvl = _level(spent_float, alarm, halt)
        return BudgetResult(
            scope=scope,
            spent_usd=spent_float,
            level=lvl,
            alarm_threshold=alarm,
            halt_threshold=halt,
            breached_alarm=spent_float >= alarm,
            breached_halt=spent_float >= halt,
            corrupted=False,
        )

    async def _publish(self, result: BudgetResult, **labels: str) -> None:
        if result.level == "ok":
            return
        record_audit(
            "budget_threshold_hit",
            scope=result.scope,
            level=result.level,
            spent_usd=result.spent_usd,
            alarm_threshold=result.alarm_threshold,
            halt_threshold=result.halt_threshold,
            corrupted=result.corrupted,
            **labels,
        )
        if self.event_loop is None:
            return
        await self.event_loop.emit(
            EventType.BUDGET_THRESHOLD_HIT,
            scope=result.scope,
            level=result.level,
            spent_usd=result.spent_usd,
            alarm_threshold=result.alarm_threshold,
            halt_threshold=result.halt_threshold,
            corrupted=result.corrupted,
            **labels,
        )


__all__ = ["BudgetGuard", "BudgetLevel", "BudgetResult", "BudgetScope"]
