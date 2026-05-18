"""Consolidator — main orchestration for the self-learning loop.

Step 1: collect raw lessons from memory_dir
Step 2: structured parse (existing parse_lessons_dir → LessonProposals)
Step 3: LLM-extraction fallback for unstructured lessons (ExtractorProtocol)
Step 4: convert patterns → Proposals (risk gate)
Step 5: return ConsolidationResult

See spec/spec_self_learning_loop.md §3.1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import structlog

from bmad_orchestrator.agent.tools._common import memory_dir
from bmad_orchestrator.runtime.lesson_parser import LessonProposal, parse_lessons_dir
from bmad_orchestrator.self_learning.config import SelfLearningConfig
from bmad_orchestrator.self_learning.extractor import (
    ExtractorProtocol,
    Lesson,
    ProposedPattern,
    StubExtractor,
)
from bmad_orchestrator.self_learning.risk_classifier import Risk, classify_risk

log = structlog.get_logger("self_learning.consolidator")


@dataclass(slots=True)
class ConsolidationResult:
    """Output of one consolidation run."""

    trigger_event: str
    structured_proposals: list[LessonProposal] = field(default_factory=list)
    extracted_patterns: list[ProposedPattern] = field(default_factory=list)
    low_risk: list[LessonProposal] = field(default_factory=list)
    medium_risk: list[LessonProposal] = field(default_factory=list)
    high_risk: list[LessonProposal] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def total_proposals(self) -> int:
        return len(self.low_risk) + len(self.medium_risk) + len(self.high_risk)


def _load_raw_lessons(lessons_root: Path) -> list[Lesson]:
    """Read all .md files under lessons_root as raw Lesson objects."""
    if not lessons_root.is_dir():
        return []
    lessons: list[Lesson] = []
    for md in sorted(lessons_root.rglob("*.md")):
        try:
            text = md.read_text(encoding="utf-8")
            # Infer level from path segment
            level = "tactical"
            parts = md.parts
            if "per-wave" in parts or "strategic" in parts:
                level = "strategic"
            elif "per-phase" in parts or "architectural" in parts:
                level = "architectural"
            lessons.append(Lesson(source_path=str(md), content=text, level=level))
        except OSError as exc:
            log.warning("lesson_read_failed", path=str(md), error=str(exc))
    return lessons


def _classify_proposals(
    proposals: list[LessonProposal],
    config: SelfLearningConfig,
) -> tuple[list[LessonProposal], list[LessonProposal], list[LessonProposal]]:
    """Split proposals into (low, medium, high) risk buckets.

    Uses risk_classifier rules. Excluded policy files → always high.
    """
    from bmad_orchestrator.self_learning.extractor import ProposedPattern

    low: list[LessonProposal] = []
    medium: list[LessonProposal] = []
    high: list[LessonProposal] = []

    excluded = set(config.excluded_policy_files)
    for p in proposals:
        # HARD GATE: excluded file names → high risk regardless
        yaml_name = f"{p.policy_file}.yaml"
        if yaml_name in excluded or p.policy_file in excluded:
            high.append(p)
            continue

        # Use risk_classifier via a synthetic ProposedPattern for the rules
        synthetic = ProposedPattern(
            pattern_id=f"{p.policy_file}.{p.field}",
            occurrence_count=1,
            policy_file=f"{p.policy_file}.yaml",
            field=p.field,
            suggested_value=p.after,
            rationale=p.rationale or "",
        )
        risk: Risk = classify_risk(synthetic, config)
        if risk == "low":
            low.append(p)
        elif risk == "medium":
            medium.append(p)
        else:
            high.append(p)

    return low, medium, high


class Consolidator:
    """Orchestrates the self-learning consolidation pipeline.

    Steps: collect → structured parse → LLM extract → risk classify.
    """

    def __init__(
        self,
        config: SelfLearningConfig,
        extractor: ExtractorProtocol | None = None,
        lessons_root: Path | None = None,
    ) -> None:
        self._config = config
        self._extractor: ExtractorProtocol = extractor or StubExtractor()
        self._lessons_root = lessons_root

    def _resolve_lessons_root(self) -> Path:
        if self._lessons_root is not None:
            return self._lessons_root
        return memory_dir()

    async def run(self, trigger_event: str) -> ConsolidationResult:
        """Run the full consolidation pipeline for ``trigger_event``."""
        result = ConsolidationResult(trigger_event=trigger_event)

        if not self._config.enabled:
            log.info("self_learning_disabled")
            return result

        lessons_root = self._resolve_lessons_root()
        log.info(
            "consolidation_started",
            trigger=trigger_event,
            lessons_root=str(lessons_root),
        )

        # Step 1+2: structured parse
        try:
            structured = parse_lessons_dir(lessons_root)
            result.structured_proposals = structured
        except Exception as exc:
            log.warning("lesson_parse_failed", error=str(exc))
            result.errors.append(f"lesson_parse: {exc}")
            structured = []

        # Step 3: LLM extraction fallback for raw lessons
        raw_lessons = _load_raw_lessons(lessons_root)
        try:
            patterns = await self._extractor.extract(raw_lessons)
            result.extracted_patterns = patterns
        except Exception as exc:
            log.warning("extractor_failed", error=str(exc))
            result.errors.append(f"extractor: {exc}")

        # Step 4: classify risk for structured proposals
        low, medium, high = _classify_proposals(structured, self._config)
        result.low_risk = low
        result.medium_risk = medium
        result.high_risk = high

        log.info(
            "consolidation_complete",
            trigger=trigger_event,
            total=result.total_proposals,
            low=len(low),
            medium=len(medium),
            high=len(high),
            patterns=len(result.extracted_patterns),
        )
        return result


__all__ = [
    "ConsolidationResult",
    "Consolidator",
]
