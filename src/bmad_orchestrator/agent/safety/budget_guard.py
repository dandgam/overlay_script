"""Deterministic budget interceptor (spec §9 layer 2 + §8).

Two-tier caps:
- Per story: $30 alarm, $50 halt
- Per batch:  $200 alarm, $300 halt
"""

from __future__ import annotations

from bmad_orchestrator.config import BudgetConfig
from bmad_orchestrator.models import Budget


class BudgetGuard:
    def __init__(self, cfg: BudgetConfig):
        self.cfg = cfg

    def check_story(self, spent_usd: float) -> Budget:
        return Budget(
            scope="story",
            spent_usd=spent_usd,
            spent_tokens=0,
            alarm_threshold=self.cfg.story_alarm_usd,
            halt_threshold=self.cfg.story_halt_usd,
            breached_alarm=spent_usd >= self.cfg.story_alarm_usd,
            breached_halt=spent_usd >= self.cfg.story_halt_usd,
        )

    def check_batch(self, spent_usd: float) -> Budget:
        return Budget(
            scope="batch",
            spent_usd=spent_usd,
            spent_tokens=0,
            alarm_threshold=self.cfg.batch_alarm_usd,
            halt_threshold=self.cfg.batch_halt_usd,
            breached_alarm=spent_usd >= self.cfg.batch_alarm_usd,
            breached_halt=spent_usd >= self.cfg.batch_halt_usd,
        )
