"""ExtractorProtocol + Lesson + ProposedPattern dataclasses + StubExtractor.

Vendor-agnostic LLM extraction layer. Mirrors the pattern from
supervisor/llm_judge.py (StubJudge). Real Sonnet extractor deferred —
swap StubExtractor implementation only; all downstream code is unchanged.

See spec/spec_self_learning_loop.md §3.4.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Protocol, cast


@dataclass(slots=True, frozen=True)
class Lesson:
    """Raw lesson as read from filesystem.

    source_path: absolute path to the .md file
    content: raw markdown text
    level: 'tactical' | 'strategic' | 'architectural'
    """

    source_path: str
    content: str
    level: str = "tactical"


@dataclass(slots=True, frozen=True)
class ProposedPattern:
    """A pattern extracted from lessons — candidate for becoming a Proposal.

    occurrence_count: how many lessons exhibit this pattern (≥ min_pattern_occurrences)
    policy_file: which policy YAML this pattern targets (may be empty for unknown)
    field: which field on the policy model
    suggested_value: proposed new value (raw YAML scalar / list)
    rationale: why this change is suggested
    """

    pattern_id: str
    occurrence_count: int
    policy_file: str
    field: str
    suggested_value: object
    rationale: str
    source_lessons: tuple[str, ...] = dc_field(default_factory=lambda: cast(tuple[str, ...], ()))


class ExtractorProtocol(Protocol):
    """LLM-based pattern extractor — reads raw lesson markdown, returns proposals."""

    async def extract(self, lessons: list[Lesson]) -> list[ProposedPattern]: ...


class StubExtractor:
    """Default — returns empty list.

    Real Sonnet implementation added behind future flag when
    supervisor real-mode is activated. Until then, all consolidation
    mechanics are exercised without burning tokens.
    """

    async def extract(self, lessons: list[Lesson]) -> list[ProposedPattern]:
        return []


__all__ = [
    "ExtractorProtocol",
    "Lesson",
    "ProposedPattern",
    "StubExtractor",
]
