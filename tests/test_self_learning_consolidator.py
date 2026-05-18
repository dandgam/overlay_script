"""Tests for self_learning.consolidator — M2."""

from __future__ import annotations

import asyncio
import textwrap
from pathlib import Path

import pytest

from bmad_orchestrator.self_learning.config import SelfLearningConfig, SelfLearningDefaults
from bmad_orchestrator.self_learning.consolidator import ConsolidationResult, Consolidator
from bmad_orchestrator.self_learning.extractor import StubExtractor


@pytest.fixture()
def default_config() -> SelfLearningConfig:
    return SelfLearningConfig(
        version=1,
        enabled=True,
        defaults=SelfLearningDefaults(),
        excluded_policy_files=["security-review.yaml", "deletion-safety.yaml"],
    )


@pytest.fixture()
def disabled_config() -> SelfLearningConfig:
    return SelfLearningConfig(
        version=1,
        enabled=False,
        defaults=SelfLearningDefaults(),
    )


def test_consolidator_disabled_returns_empty(disabled_config: SelfLearningConfig) -> None:
    c = Consolidator(config=disabled_config, lessons_root=Path("/nonexistent"))
    result = asyncio.run(c.run("wave_boundary_reached"))
    assert isinstance(result, ConsolidationResult)
    assert result.total_proposals == 0


def test_consolidator_empty_dir_returns_empty(
    default_config: SelfLearningConfig, tmp_path: Path
) -> None:
    c = Consolidator(config=default_config, lessons_root=tmp_path)
    result = asyncio.run(c.run("wave_boundary_reached"))
    assert result.total_proposals == 0
    assert result.trigger_event == "wave_boundary_reached"


def test_consolidator_nonexistent_dir_returns_empty(
    default_config: SelfLearningConfig,
) -> None:
    c = Consolidator(config=default_config, lessons_root=Path("/tmp/nonexistent_99999"))
    result = asyncio.run(c.run("monthly_review_scheduled"))
    assert result.total_proposals == 0


def test_consolidator_with_structured_proposals(
    default_config: SelfLearningConfig, tmp_path: Path
) -> None:
    # Create a lessons file with a valid structured proposal
    lesson_md = textwrap.dedent("""\
        ---
        wave: 1a
        level: strategic
        created: 2026-05-18T00:00:00
        ---

        # Wave 1a lessons

        ## Policy proposal: retry-policy.max_retries
        before: 3
        after: 5
        rationale: three waves showed insufficient retries
    """)
    (tmp_path / "wave-1a.md").write_text(lesson_md, encoding="utf-8")

    c = Consolidator(config=default_config, lessons_root=tmp_path)
    result = asyncio.run(c.run("wave_boundary_reached"))
    assert len(result.structured_proposals) == 1
    assert result.total_proposals == 1


def test_consolidator_threshold_field_goes_medium_risk(
    default_config: SelfLearningConfig, tmp_path: Path
) -> None:
    # max_retries is in _THRESHOLD_FIELDS → medium risk after parsing
    lesson_md = textwrap.dedent("""\
        ---
        wave: 1a
        level: strategic
        created: 2026-05-18T00:00:00
        ---

        # Test

        ## Policy proposal: retry-policy.max_retries
        before: 3
        after: 5
        rationale: not enough retries
    """)
    (tmp_path / "test.md").write_text(lesson_md, encoding="utf-8")

    c = Consolidator(config=default_config, lessons_root=tmp_path)
    result = asyncio.run(c.run("wave_boundary_reached"))
    # max_retries is in _THRESHOLD_FIELDS → medium risk
    assert len(result.medium_risk) == 1
    assert len(result.low_risk) == 0


def test_consolidator_stub_extractor_returns_empty_patterns(
    default_config: SelfLearningConfig, tmp_path: Path
) -> None:
    c = Consolidator(
        config=default_config,
        extractor=StubExtractor(),
        lessons_root=tmp_path,
    )
    result = asyncio.run(c.run("phase4_complete"))
    assert result.extracted_patterns == []
