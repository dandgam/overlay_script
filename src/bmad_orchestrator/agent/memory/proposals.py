"""Proactive-improver proposal builder (spec §19 — proactive-improver skill).

После каждого retro skill вызывает `build_proposals(retro_id)` —
читает retrospective.md + опциональный policy-deltas.yaml + lessons.md и
возвращает list[Proposal]. Telegram bot (S8) превращает их в inline-button push.

Risk classification per skill spec:
- low  — auto-apply, если в Telegram нажат [✓ apply]
- medium / high — explicit approval с diff preview; high == code → PR-flow, не auto.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final, Literal, cast

import yaml

from bmad_orchestrator.agent.memory.gates import retro_artifact_path
from bmad_orchestrator.agent.memory.schedule import RetroId, RetroLevel
from bmad_orchestrator.agent.tools._common import memory_dir

ProposalType = Literal["policy", "config", "code"]
ProposalRisk = Literal["low", "medium", "high"]

_VALID_TYPES: Final[frozenset[str]] = frozenset({"policy", "config", "code"})
_VALID_RISKS: Final[frozenset[str]] = frozenset({"low", "medium", "high"})


@dataclass(slots=True)
class Proposal:
    """Single improvement proposal — consumed by Telegram inline-button push."""

    id: str
    type: ProposalType
    title: str
    risk: ProposalRisk
    effect: str
    diff_summary: str
    auto_apply_if_approved: bool
    source_retro: str
    extra: dict[str, str] = field(default_factory=dict)


def _coerce_type(raw: object) -> ProposalType:
    s = str(raw).lower()
    if s in _VALID_TYPES:
        return cast(ProposalType, s)
    return "policy"


def _coerce_risk(raw: object) -> ProposalRisk:
    s = str(raw).lower()
    if s in _VALID_RISKS:
        return cast(ProposalRisk, s)
    return "low"


def _deltas_yaml_path(retro_id: RetroId) -> str:
    """Canonical deltas YAML path per retro level."""
    if retro_id.level is RetroLevel.WAVE:
        return f"per-wave/{retro_id.key}-policy-deltas.yaml"
    if retro_id.level is RetroLevel.EPIC:
        return f"retrospectives/epic-{retro_id.key}-deltas.yaml"
    return "retrospectives/phase-5-deltas.yaml"


def _load_deltas(retro_id: RetroId) -> list[dict[str, Any]]:
    path = memory_dir() / _deltas_yaml_path(retro_id)
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        return []
    deltas = raw.get("deltas") or []
    return [d for d in deltas if isinstance(d, dict)]


def build_proposals(retro_id: RetroId) -> list[Proposal]:
    """Build proposal list from a completed retro's deltas YAML.

    Returns empty list if the retro artifact missing — proactive-improver
    must not fire before retro is on disk (spec §6.1 hard gate).
    """
    if not retro_artifact_path(retro_id).exists():
        return []

    proposals: list[Proposal] = []
    for idx, delta in enumerate(_load_deltas(retro_id), start=1):
        ptype = _coerce_type(delta.get("type", "policy"))
        risk = _coerce_risk(delta.get("risk", "low"))
        # Code-level proposals никогда не auto-apply per skill spec.
        if ptype == "code":
            auto_apply = False
        else:
            auto_apply = bool(delta.get("auto_apply_if_approved", risk == "low"))

        title_raw = (
            delta.get("title")
            or delta.get("rule_id")
            or f"{retro_id.slug} proposal #{idx:02d}"
        )
        proposals.append(
            Proposal(
                id=f"{retro_id.slug}-{ptype}-{idx:02d}",
                type=ptype,
                title=str(title_raw),
                risk=risk,
                effect=str(delta.get("effect") or ""),
                diff_summary=str(delta.get("diff_summary") or ""),
                auto_apply_if_approved=auto_apply,
                source_retro=retro_id.slug,
                extra={
                    k: str(v)
                    for k, v in delta.items()
                    if k
                    not in {
                        "type",
                        "title",
                        "rule_id",
                        "risk",
                        "effect",
                        "diff_summary",
                        "auto_apply_if_approved",
                    }
                },
            )
        )
    return proposals


__all__ = [
    "Proposal",
    "ProposalRisk",
    "ProposalType",
    "build_proposals",
]
