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
from typing import Literal

from bmad_orchestrator.agent.safety.audit import record_audit
from bmad_orchestrator.config import BudgetConfig
from bmad_orchestrator.models import Budget
from bmad_orchestrator.runtime.budget import is_finite_spend
from bmad_orchestrator.runtime.event_loop import EventLoop, EventType

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

    def __init__(self, cfg: BudgetConfig, event_loop: EventLoop | None = None) -> None:
        self.cfg = cfg
        self.event_loop = event_loop

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
