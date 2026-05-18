"""Tests for self_learning.extractor — M1."""

from __future__ import annotations

import asyncio

from bmad_orchestrator.self_learning.extractor import (
    ExtractorProtocol,
    Lesson,
    ProposedPattern,
    StubExtractor,
)


def test_stub_extractor_returns_empty() -> None:
    extractor = StubExtractor()
    lessons = [Lesson(source_path="/tmp/a.md", content="some text")]
    result = asyncio.run(extractor.extract(lessons))
    assert result == []


def test_stub_extractor_empty_input() -> None:
    extractor = StubExtractor()
    result = asyncio.run(extractor.extract([]))
    assert result == []


def test_stub_extractor_protocol_compatible() -> None:
    # duck-type check: StubExtractor satisfies ExtractorProtocol
    extractor: ExtractorProtocol = StubExtractor()
    lessons = [Lesson(source_path="/tmp/x.md", content="lesson content")]
    result = asyncio.run(extractor.extract(lessons))
    assert isinstance(result, list)


def test_lesson_fields() -> None:
    lesson = Lesson(
        source_path="/memory/per-wave/1a.md",
        content="## Notes\nSome lesson",
        level="strategic",
    )
    assert lesson.level == "strategic"
    assert "Notes" in lesson.content


def test_proposed_pattern_fields() -> None:
    p = ProposedPattern(
        pattern_id="pat-001",
        occurrence_count=3,
        policy_file="cost-tuning",
        field="story_alarm_usd",
        suggested_value=40.0,
        rationale="three waves all exceeded 30 USD alarm",
        source_lessons=("/a.md", "/b.md", "/c.md"),
    )
    assert p.occurrence_count == 3
    assert len(p.source_lessons) == 3


def test_custom_extractor_protocol_duck_type() -> None:
    """A custom class satisfies ExtractorProtocol via duck-typing."""

    class FixedExtractor:
        async def extract(self, lessons: list[Lesson]) -> list[ProposedPattern]:
            return [
                ProposedPattern(
                    pattern_id="fixed-001",
                    occurrence_count=5,
                    policy_file="retry-policy",
                    field="max_retries",
                    suggested_value=5,
                    rationale="always retry 5 times",
                )
            ]

    extractor: ExtractorProtocol = FixedExtractor()  # type: ignore[assignment]
    lessons = [Lesson(source_path="/tmp/l.md", content="text")]
    result = asyncio.run(extractor.extract(lessons))
    assert len(result) == 1
    assert result[0].pattern_id == "fixed-001"
