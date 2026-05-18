"""Initiative #3C — validation pilot (S9).

Spec acceptance §Task 3.5 + tracker S9 (`backend-python`):

* Оба waves complete (verified via real subprocess returncode → ProjectRunResult).
* sprint-status каждого проекта корректен (disjoint files at disjoint paths,
  content reflects own slug only).
* No state leak в memory/.claude/ (slug-keyed memory writes never overlap).

The **real Antares + Odyssey 5/5-worker wave acceptance is deferred** to a
manual user run — Antares stories 1.2 / 1.3 / 1.5 / 3.1 are missing
(resolved_deferred in S1 / S3 / S6) and Odyssey Wave 1a has never been
piloted manually yet (project CLAUDE.md status). Real-wave validation lands
when those preconditions complete. See Blockers / Pauses entry on S9 in
``.claude/initiative-tracker-parallelism_initiatives.md`` and the S9 journal.

What this module pins under autoloop / CI:

* The exact pipeline (slot allocation → isolation gate → shared budget →
  ``asyncio.gather`` over real OS subprocesses → outcome aggregation) is
  exercised via a *shim runner* that mirrors ``cli.main._subprocess_runner``
  structurally — same ``asyncio.create_subprocess_exec`` call, same env
  propagation, same returncode mapping — but spawns a tiny ``python -c``
  child instead of ``bmad-orchestrator run``. Cost: zero $; wall-time
  ≈150ms × N children; isolation invariants identical.
* The real ``_subprocess_runner`` is asserted to satisfy the ``RunnerFn``
  contract so a future refactor that drops the signature fails loudly here
  rather than at first real wave invocation.
* sprint-status disjoint-content + memory slug-keyed isolation are
  re-verified under the real-subprocess path (S8 covered them with stub
  runners; S9 closes the "but does it still hold when an actual fork()
  happens?" gap).
"""

from __future__ import annotations

import asyncio
import inspect
import os
import sys
import textwrap
from pathlib import Path

import pytest

from bmad_orchestrator.cli.main import _subprocess_runner
from bmad_orchestrator.runtime.multi_run import (
    MultiProjectPlan,
    ProjectRunResult,
    SharedSpendTracker,
    run_multi,
)
from bmad_orchestrator.runtime.project_memory import (
    ProjectMemory,
    load_project_memory,
    save_project_memory,
)
from bmad_orchestrator.runtime.project_registry import (
    ProjectEntry,
    ProjectsRegistry,
)

# ── helpers ──────────────────────────────────────────────────────────────────


def _make_fixture_registry(
    tmp_path: Path, slugs: tuple[str, ...]
) -> ProjectsRegistry:
    """Create a registry of synthetic bmm-v6 fixture projects under ``tmp_path``.

    Each fixture has ``_bmad/bmm/config.yaml`` (so layout detector classifies
    it as ``bmm-v6``) and ``_bmad-output/implementation-artifacts/`` (so the
    subprocess child has a canonical place to write sprint-status).
    """
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
        (root / "_bmad-output" / "planning-artifacts" / "stories").mkdir(
            parents=True, exist_ok=True
        )
        projects[slug] = ProjectEntry(path=root.resolve(), bmad_layout="bmm-v6")
    return ProjectsRegistry(projects=projects)


# Shim subprocess body. Receives wave + slug via argv and writes a
# sprint-status.yaml that pins (a) ORCHESTRATOR_TARGET_PROJECT == own slot.path
# and (b) own slug. Cross-contamination would manifest as the wrong slug
# appearing in the wrong file.
_SHIM_SCRIPT = textwrap.dedent(
    """
    import os, sys, pathlib
    wave = sys.argv[1]
    slug = sys.argv[2]
    target = pathlib.Path(os.environ["ORCHESTRATOR_TARGET_PROJECT"])
    out = target / "_bmad-output" / "implementation-artifacts" / "sprint-status.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        f"project_path: {target}\\nwave: {wave}\\nslug: {slug}\\n",
        encoding="utf-8",
    )
    sys.exit(0)
    """
).strip()


# Shim that always fails — used by failure-isolation test.
_FAILING_SHIM = textwrap.dedent(
    """
    import sys
    sys.stderr.write("simulated wave failure\\n")
    sys.exit(2)
    """
).strip()


def _shim_runner_factory(
    script: str, *, spend_per_call: float = 0.10
):
    """Build a real-subprocess runner that spawns ``python -c script``.

    Mirrors ``cli.main._subprocess_runner`` structurally: same
    ``asyncio.create_subprocess_exec``, same env propagation
    (``ORCHESTRATOR_TARGET_PROJECT``), same returncode → ProjectRunResult
    mapping. The shim avoids the cost + setup overhead of a real
    ``bmad-orchestrator run`` invocation while exercising the real OS-level
    process boundary.
    """

    async def _runner(slot, tracker: SharedSpendTracker, plan: MultiProjectPlan):
        env = dict(os.environ)
        env["ORCHESTRATOR_TARGET_PROJECT"] = str(slot.path)
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", script, plan.wave, slot.slug,
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        completed = proc.returncode == 0
        await tracker.add(spend_per_call)
        err = None if completed else (
            f"exit {proc.returncode}: "
            f"{stderr.decode('utf-8', errors='replace')[:200]}"
        )
        return ProjectRunResult(
            slug=slot.slug,
            completed=completed,
            spent_usd=spend_per_call,
            stories_done=1 if completed else 0,
            error=err,
        )

    return _runner


# ── real subprocess pilot — happy path + isolation ──────────────────────────


class TestPilotRealSubprocessIsolation:
    """Two fixture projects, real OS subprocesses, isolation invariants pinned."""

    async def test_both_waves_complete_real_subprocess(
        self, tmp_path: Path
    ) -> None:
        reg = _make_fixture_registry(
            tmp_path, ("antares-fixture", "odyssey-fixture")
        )
        plan = MultiProjectPlan(
            projects=("antares-fixture", "odyssey-fixture"),
            total_parallel=2,
            wave="1a",
            daily_max_spend_usd=5.0,
        )
        outcome = await run_multi(
            plan,
            registry=reg,
            runner_fn=_shim_runner_factory(_SHIM_SCRIPT),
        )
        assert outcome.succeeded
        assert set(outcome.per_project) == {"antares-fixture", "odyssey-fixture"}
        for slug, result in outcome.per_project.items():
            assert result.completed, f"{slug} failed: {result.error}"
            assert result.stories_done == 1
        assert outcome.total_spent_usd == pytest.approx(0.20)

    async def test_subprocess_env_propagation_writes_to_own_path(
        self, tmp_path: Path
    ) -> None:
        """Each child sees its own ``ORCHESTRATOR_TARGET_PROJECT`` and writes
        sprint-status under exactly that path — never the sibling's."""
        reg = _make_fixture_registry(tmp_path, ("antares-fixture", "odyssey-fixture"))
        plan = MultiProjectPlan(
            projects=("antares-fixture", "odyssey-fixture"),
            total_parallel=2,
            wave="1a",
            daily_max_spend_usd=5.0,
        )
        await run_multi(
            plan,
            registry=reg,
            runner_fn=_shim_runner_factory(_SHIM_SCRIPT),
        )
        antares_status = (
            tmp_path / "antares-fixture" / "_bmad-output"
            / "implementation-artifacts" / "sprint-status.yaml"
        )
        odyssey_status = (
            tmp_path / "odyssey-fixture" / "_bmad-output"
            / "implementation-artifacts" / "sprint-status.yaml"
        )
        assert antares_status.exists()
        assert odyssey_status.exists()
        antares_content = antares_status.read_text(encoding="utf-8")
        odyssey_content = odyssey_status.read_text(encoding="utf-8")
        # Disjoint content — no cross-contamination across forked children.
        assert "slug: antares-fixture" in antares_content
        assert "slug: odyssey-fixture" not in antares_content
        assert "slug: odyssey-fixture" in odyssey_content
        assert "slug: antares-fixture" not in odyssey_content
        # Each file pins its own ORCHESTRATOR_TARGET_PROJECT.
        assert str((tmp_path / "antares-fixture").resolve()) in antares_content
        assert str((tmp_path / "odyssey-fixture").resolve()) in odyssey_content

    async def test_subprocess_returncode_zero_completed_true(
        self, tmp_path: Path
    ) -> None:
        """Shim exits 0 → ProjectRunResult.completed=True; matches the real
        ``_subprocess_runner`` returncode-mapping contract."""
        reg = _make_fixture_registry(tmp_path, ("only",))
        plan = MultiProjectPlan(
            projects=("only",), total_parallel=1, wave="1a",
            daily_max_spend_usd=5.0,
        )
        outcome = await run_multi(
            plan,
            registry=reg,
            runner_fn=_shim_runner_factory(_SHIM_SCRIPT),
        )
        assert outcome.succeeded
        assert outcome.per_project["only"].completed
        assert outcome.per_project["only"].error is None


# ── memory isolation across real-subprocess waves ────────────────────────────


class TestPilotMemoryIsolation:
    """save_project_memory's slug keying survives concurrent real-subprocess
    waves. The orchestrator process owns the memory writes (children only
    write sprint-status), but we exercise it across the same parallel pipeline
    to mirror real-wave behavior where the parent records the wave outcome
    per project after each child returns.
    """

    async def test_memory_files_keyed_by_slug_no_cross_pollination(
        self, tmp_path: Path
    ) -> None:
        reg = _make_fixture_registry(tmp_path, ("antares-fixture", "odyssey-fixture"))
        plan = MultiProjectPlan(
            projects=("antares-fixture", "odyssey-fixture"),
            total_parallel=2,
            wave="1a",
            daily_max_spend_usd=5.0,
        )
        home = tmp_path / "orchestrator-home"
        recorded_paths: list[Path] = []

        async def runner_with_memory_write(slot, tracker, plan_):
            # Same real-subprocess plumbing as the happy-path shim.
            env = dict(os.environ)
            env["ORCHESTRATOR_TARGET_PROJECT"] = str(slot.path)
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-c", _SHIM_SCRIPT, plan_.wave, slot.slug,
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            # Parent records per-project memory after the child returns.
            mem = ProjectMemory(
                project_slug=slot.slug,
                median_story_cost_usd=0.10,
                last_wave=plan_.wave,
            )
            path = save_project_memory(mem, orchestrator_home=home)
            recorded_paths.append(path)
            await tracker.add(0.10)
            return ProjectRunResult(
                slug=slot.slug, completed=proc.returncode == 0,
                spent_usd=0.10, stories_done=1,
            )

        outcome = await run_multi(
            plan, registry=reg, runner_fn=runner_with_memory_write
        )
        assert outcome.succeeded
        # Two distinct memory files, both under home/_config/projects/<slug>/.
        assert len(recorded_paths) == 2
        assert len(set(recorded_paths)) == 2
        antares_mem = load_project_memory("antares-fixture", orchestrator_home=home)
        odyssey_mem = load_project_memory("odyssey-fixture", orchestrator_home=home)
        # Each memory pins its own slug — slug keying defeated cross-write.
        assert antares_mem.project_slug == "antares-fixture"
        assert odyssey_mem.project_slug == "odyssey-fixture"
        assert antares_mem.last_wave == "1a"
        assert odyssey_mem.last_wave == "1a"

    async def test_memory_load_for_unknown_project_returns_defaults(
        self, tmp_path: Path
    ) -> None:
        """Sanity invariant — a project that never had memory persisted yet
        gets fresh defaults rather than picking up a sibling's file."""
        home = tmp_path / "orchestrator-home"
        antares_mem = ProjectMemory(
            project_slug="antares-fixture", median_story_cost_usd=0.10,
        )
        save_project_memory(antares_mem, orchestrator_home=home)
        # Loading a different slug must NOT return antares's payload.
        odyssey_mem = load_project_memory(
            "odyssey-fixture", orchestrator_home=home
        )
        assert odyssey_mem.project_slug == "odyssey-fixture"
        assert odyssey_mem.median_story_cost_usd == 0.0  # default


# ── failure-mode isolation under real subprocesses ───────────────────────────


class TestPilotFailureModes:
    """Failure-isolation invariants under the real-subprocess pipeline."""

    async def test_one_project_subprocess_fails_sibling_still_completes(
        self, tmp_path: Path
    ) -> None:
        """Critical wave-parallel invariant: a failing child must NOT cancel
        the sibling's wave. Matches ``run_multi``'s ``_wrap`` philosophy —
        one project's outage does NOT compound into a total outage."""
        reg = _make_fixture_registry(tmp_path, ("good-project", "bad-project"))
        plan = MultiProjectPlan(
            projects=("good-project", "bad-project"),
            total_parallel=2,
            wave="1a",
            daily_max_spend_usd=5.0,
        )

        async def mixed_runner(slot, tracker, plan_):
            script = _FAILING_SHIM if slot.slug == "bad-project" else _SHIM_SCRIPT
            env = dict(os.environ)
            env["ORCHESTRATOR_TARGET_PROJECT"] = str(slot.path)
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-c", script, plan_.wave, slot.slug,
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            completed = proc.returncode == 0
            await tracker.add(0.10)
            return ProjectRunResult(
                slug=slot.slug,
                completed=completed,
                spent_usd=0.10,
                stories_done=1 if completed else 0,
                error=None if completed else f"exit {proc.returncode}",
            )

        outcome = await run_multi(plan, registry=reg, runner_fn=mixed_runner)
        assert not outcome.succeeded  # bad-project failed
        assert outcome.per_project["good-project"].completed
        assert not outcome.per_project["bad-project"].completed
        assert outcome.per_project["bad-project"].error is not None
        # The good wave's sprint-status was still written.
        good_status = (
            tmp_path / "good-project" / "_bmad-output"
            / "implementation-artifacts" / "sprint-status.yaml"
        )
        assert good_status.exists()
        assert "slug: good-project" in good_status.read_text(encoding="utf-8")

    async def test_pre_flight_halt_blocks_subprocess_spawn(
        self, tmp_path: Path
    ) -> None:
        """If the shared budget is already at halt on entry, NO subprocess
        ever starts. Cost-control invariant for second-wave starts when
        the daily cap is drained by an earlier wave.

        ``BudgetGuard.enforce_day`` is stateless re: ``spent_usd`` — the only
        deterministic way to hit pre-flight halt is a zero daily cap, which
        models "operator already drained today's budget" without depending on
        guard-internal accumulated state.
        """
        reg = _make_fixture_registry(tmp_path, ("antares-fixture", "odyssey-fixture"))
        plan = MultiProjectPlan(
            projects=("antares-fixture", "odyssey-fixture"),
            total_parallel=2,
            wave="1a",
            daily_max_spend_usd=0.0,
        )
        spawn_count = 0

        async def counting_runner(slot, t, p):  # pragma: no cover
            nonlocal spawn_count
            spawn_count += 1
            return ProjectRunResult(slug=slot.slug, completed=True)

        outcome = await run_multi(
            plan, registry=reg, runner_fn=counting_runner
        )
        assert not outcome.succeeded
        assert outcome.aborted_reason is not None
        assert "halt" in outcome.aborted_reason
        assert spawn_count == 0  # never reached the runner


# ── _subprocess_runner contract pin ──────────────────────────────────────────


class TestSubprocessRunnerContract:
    """``cli.main._subprocess_runner`` must satisfy the ``RunnerFn`` signature.

    The shim tests above exercise the *plumbing* (env + returncode + isolation);
    this test pins the real production runner's shape so a future refactor that
    drops a parameter or changes return type fails loudly here rather than at
    first wave dispatch.
    """

    def test_signature_matches_runner_fn(self) -> None:
        sig = inspect.signature(_subprocess_runner)
        assert list(sig.parameters) == ["slot", "tracker", "plan"]
        assert inspect.iscoroutinefunction(_subprocess_runner)
