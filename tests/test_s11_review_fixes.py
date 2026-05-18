"""Regression tests for S11 review fixes (Phase 4B).

One section per finding from
``.claude/checkpoints/parallelism_initiatives-review-S10.md``. Each test pins
the post-fix behaviour so a future refactor that re-introduces the bug fails
loudly.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

from bmad_orchestrator.cli.main import (
    _read_spend_report,
)
from bmad_orchestrator.runtime.multi_run import (
    MultiProjectPlan,
    ProjectRunResult,
    run_multi,
)
from bmad_orchestrator.runtime.project_registry import (
    ProjectEntry,
    ProjectsRegistry,
)

# ── helpers ──────────────────────────────────────────────────────────────────


def _fixture_registry(tmp_path: Path, slugs: tuple[str, ...]) -> ProjectsRegistry:
    """Build a tiny registry of bmm-v6 fixture projects under ``tmp_path``."""
    projects: dict[str, ProjectEntry] = {}
    for slug in slugs:
        root = tmp_path / slug
        (root / "_bmad" / "bmm").mkdir(parents=True, exist_ok=True)
        (root / "_bmad" / "bmm" / "config.yaml").write_text(
            f"project_name: {slug}\n", encoding="utf-8"
        )
        (root / "_bmad-output" / "implementation-artifacts").mkdir(
            parents=True, exist_ok=True
        )
        projects[slug] = ProjectEntry(path=root.resolve(), bmad_layout="bmm-v6")
    return ProjectsRegistry(projects=projects)


# ── P1-A: SharedSpendTracker receives production spend ──────────────────────


class TestP1ASpendHandoff:
    """Child orchestrators write ``spend.json``; parent folds into shared cap."""

    def test_read_spend_report_returns_zero_on_missing(self, tmp_path: Path) -> None:
        path = tmp_path / "missing" / "spend.json"
        assert _read_spend_report(path) == 0.0

    def test_read_spend_report_parses_payload(self, tmp_path: Path) -> None:
        wrap = tmp_path / "bmad-multi-slug-abc"
        wrap.mkdir()
        path = wrap / "spend.json"
        path.write_text(json.dumps({"spent_usd": 12.5}), encoding="utf-8")
        assert _read_spend_report(path) == 12.5
        assert not path.exists(), "report file should be cleaned up after read"
        assert not wrap.exists(), "tempdir should be removed when prefix matches"

    def test_read_spend_report_returns_zero_on_malformed(
        self, tmp_path: Path
    ) -> None:
        wrap = tmp_path / "bmad-multi-slug-bad"
        wrap.mkdir()
        path = wrap / "spend.json"
        path.write_text("{not-json", encoding="utf-8")
        assert _read_spend_report(path) == 0.0

    async def test_shim_runner_spend_reaches_tracker(
        self, tmp_path: Path
    ) -> None:
        """Real-subprocess shim writes spend.json → parent updates shared tracker."""
        registry = _fixture_registry(tmp_path, ("alpha", "beta"))

        shim = textwrap.dedent(
            """
            import json, os, pathlib, sys
            target = pathlib.Path(os.environ["BMAD_MULTI_SPEND_REPORT"])
            target.write_text(json.dumps({"spent_usd": 7.5}), encoding="utf-8")
            sys.exit(0)
            """
        ).strip()

        async def runner(slot, tracker, plan):
            spend_report = Path(
                tempfile.mkdtemp(prefix=f"bmad-multi-{slot.slug}-")
            ) / "spend.json"
            env = dict(os.environ)
            env["BMAD_MULTI_SPEND_REPORT"] = str(spend_report)
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-c", shim,
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            spent = _read_spend_report(spend_report)
            await tracker.add(spent)
            return ProjectRunResult(
                slug=slot.slug, completed=proc.returncode == 0, spent_usd=spent
            )

        plan = MultiProjectPlan(
            projects=("alpha", "beta"),
            total_parallel=2,
            wave="1a",
            daily_max_spend_usd=20.0,
            mock=False,
        )
        outcome = await run_multi(plan, registry=registry, runner_fn=runner)
        assert outcome.total_spent_usd == pytest.approx(15.0)
        assert outcome.per_project["alpha"].spent_usd == pytest.approx(7.5)


# ── P1-B: per-child timeout ─────────────────────────────────────────────────


class TestP1BTimeout:
    """``per_project_timeout_sec`` validated + slow child SIGKILLed."""

    def test_timeout_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="per_project_timeout_sec"):
            MultiProjectPlan(
                projects=("a",),
                total_parallel=1,
                wave="1a",
                per_project_timeout_sec=0,
            )

    async def test_slow_child_times_out_and_sibling_completes(
        self, tmp_path: Path
    ) -> None:
        """Hung shim should not park the wave; sibling still completes."""
        registry = _fixture_registry(tmp_path, ("fast", "slow"))

        async def runner(slot, tracker, plan):
            if slot.slug == "slow":
                proc = await asyncio.create_subprocess_exec(
                    sys.executable, "-c", "import time; time.sleep(60)",
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                timed_out = False
                try:
                    await asyncio.wait_for(
                        proc.wait(), timeout=plan.per_project_timeout_sec
                    )
                except TimeoutError:
                    timed_out = True
                    proc.kill()
                    await proc.wait()
                return ProjectRunResult(
                    slug=slot.slug,
                    completed=not timed_out,
                    error="timeout" if timed_out else None,
                )
            return ProjectRunResult(slug=slot.slug, completed=True)

        plan = MultiProjectPlan(
            projects=("fast", "slow"),
            total_parallel=2,
            wave="1a",
            per_project_timeout_sec=0.5,
        )
        outcome = await run_multi(plan, registry=registry, runner_fn=runner)
        assert outcome.per_project["fast"].completed is True
        assert outcome.per_project["slow"].completed is False
        assert "timeout" in (outcome.per_project["slow"].error or "")


__all__: list[str] = []
