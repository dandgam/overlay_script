"""Three-level learning writers (spec §6).

- **Tactical** (per story): tokens / cost / time / elicitations breakdown,
  пишется по завершении story; consumed by reflexion-learner для wave-level
  aggregation.
- **Strategic** (per wave): рейкап patterns + optimal parallelism +
  cost distribution; формируется retrospective-writer'ом, перевариваясь
  через compress_wave_lessons на per-wave/<wave>.md.
- **Architectural** (per phase): cross-cutting concerns, security review
  categories, типы stories требующие human review; пишется на phase boundary.

Все три уровня писать в `memory_dir()` под канонические подпапки. Reader API —
`agent/tools/memory.py.read_memory` (path-traversal protected).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from bmad_orchestrator.agent.tools._common import memory_dir, now_iso


@dataclass(slots=True)
class TacticalLesson:
    """Per-story snapshot (spec §6 «Тактика»)."""

    story_id: str
    tokens_used: int
    cost_usd: float
    duration_seconds: int
    elicitations_auto: int = 0
    elicitations_escalated: int = 0
    notes: str = ""

    def render(self) -> str:
        return (
            f"---\nstory: {self.story_id}\nlevel: tactical\ncreated: {now_iso()}\n"
            f"tokens: {self.tokens_used}\ncost_usd: {self.cost_usd}\n"
            f"duration_s: {self.duration_seconds}\n"
            f"elicitations_auto: {self.elicitations_auto}\n"
            f"elicitations_escalated: {self.elicitations_escalated}\n---\n\n"
            f"# Lesson — {self.story_id}\n\n{self.notes or '_no notes_'}\n"
        )


@dataclass(slots=True)
class StrategicLesson:
    """Per-wave summary (spec §6 «Стратегия»)."""

    wave: str
    patterns: list[str] = field(default_factory=list)
    optimal_parallelism: int = 1
    cost_distribution: str = ""
    notes: str = ""

    def render(self) -> str:
        patterns_block = "\n".join(f"- {p}" for p in self.patterns) or "_none_"
        return (
            f"---\nwave: {self.wave}\nlevel: strategic\ncreated: {now_iso()}\n"
            f"optimal_parallelism: {self.optimal_parallelism}\n---\n\n"
            f"# Wave {self.wave} strategy lessons\n\n"
            f"## Patterns\n{patterns_block}\n\n"
            f"## Cost distribution\n{self.cost_distribution or '_unknown_'}\n\n"
            f"## Notes\n{self.notes or '_none_'}\n"
        )


@dataclass(slots=True)
class ArchitecturalLesson:
    """Per-phase pattern roll-up (spec §6 «Архитектура»)."""

    phase: str
    cross_cutting_concerns: list[str] = field(default_factory=list)
    security_review_categories: list[str] = field(default_factory=list)
    notes: str = ""

    def render(self) -> str:
        concerns = (
            "\n".join(f"- {c}" for c in self.cross_cutting_concerns) or "_none_"
        )
        sec = (
            "\n".join(f"- {s}" for s in self.security_review_categories) or "_none_"
        )
        return (
            f"---\nphase: {self.phase}\nlevel: architectural\ncreated: {now_iso()}\n---\n\n"
            f"# Phase {self.phase} architectural patterns\n\n"
            f"## Cross-cutting concerns\n{concerns}\n\n"
            f"## Security review categories\n{sec}\n\n"
            f"## Notes\n{self.notes or '_none_'}\n"
        )


def record_tactical_lesson(lesson: TacticalLesson) -> Path:
    out = memory_dir() / "per-story" / f"{lesson.story_id}.lesson.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(lesson.render(), encoding="utf-8")
    return out


def record_strategic_lesson(lesson: StrategicLesson) -> Path:
    out = memory_dir() / "per-wave" / f"{lesson.wave}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(lesson.render(), encoding="utf-8")
    return out


def record_architectural_lesson(lesson: ArchitecturalLesson) -> Path:
    out = memory_dir() / "per-phase" / f"{lesson.phase}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(lesson.render(), encoding="utf-8")
    return out


__all__ = [
    "ArchitecturalLesson",
    "StrategicLesson",
    "TacticalLesson",
    "record_architectural_lesson",
    "record_strategic_lesson",
    "record_tactical_lesson",
]
