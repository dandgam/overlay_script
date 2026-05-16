"""Deterministic budget interceptor (spec §9 layer 2 + §8).

Two-tier caps:
- Per story:  $30 alarm, $50 halt
- Per batch:  $200 alarm, $300 halt

`BudgetGuard.enforce_story` / `enforce_batch` возвращают `BudgetResult`
с уровнем (`ok` | `alarm` | `halt`). На уровне `halt` они дополнительно
эмитят `budget_threshold_hit` event в подключённый `EventLoop`.

Mock workflow тест-демонстратор: см. tests/test_s4_safety.py::test_budget_halt_emits_event.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from bmad_orchestrator.agent.safety.audit import record_audit
from bmad_orchestrator.config import BudgetConfig
from bmad_orchestrator.models import Budget
from bmad_orchestrator.runtime.event_loop import EventLoop, EventType

BudgetLevel = Literal["ok", "alarm", "halt"]


@dataclass(slots=True)
class BudgetResult:
    scope: Literal["story", "batch"]
    spent_usd: float
    level: BudgetLevel
    alarm_threshold: float
    halt_threshold: float
    breached_alarm: bool
    breached_halt: bool

    def as_budget(self) -> Budget:
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

    # Sync probes для unit-тестов / TUI dashboard.
    def check_story(self, spent_usd: float) -> BudgetResult:
        return self._evaluate(
            spent_usd, self.cfg.story_alarm_usd, self.cfg.story_halt_usd, scope="story"
        )

    def check_batch(self, spent_usd: float) -> BudgetResult:
        return self._evaluate(
            spent_usd, self.cfg.batch_alarm_usd, self.cfg.batch_halt_usd, scope="batch"
        )

    # ── internals ──────────────────────────────────────────────────────────────

    @staticmethod
    def _evaluate(
        spent_usd: float, alarm: float, halt: float, *, scope: Literal["story", "batch"]
    ) -> BudgetResult:
        lvl = _level(spent_usd, alarm, halt)
        return BudgetResult(
            scope=scope,
            spent_usd=spent_usd,
            level=lvl,
            alarm_threshold=alarm,
            halt_threshold=halt,
            breached_alarm=spent_usd >= alarm,
            breached_halt=spent_usd >= halt,
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
            **labels,
        )


__all__ = ["BudgetGuard", "BudgetLevel", "BudgetResult"]
