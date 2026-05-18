"""Auto-elicitation policy engine (Phase 3 — P2 Routing).

См. spec/spec_auto_elicitation_engine.md.

Public API:
    * ElicitationEngine   — main router (Tier 0/1/2 + window cap)
    * ElicitationPolicy   — pydantic schema for YAML config
    * Decision            — engine output (action + reason + tier)
    * load_policy         — YAML loader with validation
"""

from __future__ import annotations

from bmad_orchestrator.elicitation.engine import ElicitationEngine
from bmad_orchestrator.elicitation.policy import (
    Decision,
    Defaults,
    ElicitationPolicy,
    Rule,
    load_policy,
)

__all__ = [
    "Decision",
    "Defaults",
    "ElicitationEngine",
    "ElicitationPolicy",
    "Rule",
    "load_policy",
]
