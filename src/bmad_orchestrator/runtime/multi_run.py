"""Multi-project execution — Initiative #3 Task 3.3-3.4.

Runs the orchestrator across N target projects concurrently (e.g. Antares +
Odyssey) with:

* **Slot allocation** — splits ``total_parallel`` workers across the listed
  projects as evenly as possible (10/2 → 5/5, 10/3 → 4/3/3). Each project
  gets at least one slot; ``total_parallel < len(projects)`` is rejected.
* **Shared budget guard** — a single :class:`BudgetGuard` instance is
  threaded through every per-project runner via :class:`SharedSpendTracker`,
  so the *aggregate* daily cap halts the second project once the first has
  drained the shared budget — even if neither alone would breach.
* **Per-project state isolation** — the runner_fn is responsible for using
  the slot's project path for every read/write (sprint-status, project
  memory). The module enforces no cross-pollination at the input boundary
  via :func:`validate_project_isolation` (refuses any project path inside
  the forbidden prod CRM mount).
* **Injection seam** — ``runner_fn`` is the only contact with real
  ``claude -p`` spawning. Tests inject stubs to exercise the slot/budget
  pipeline without touching the SDK.

Sibling of :mod:`runtime.project_registry` (Init #3 Task 3.1-3.2); the
CLI ``multi`` command wires the real subprocess-per-project runner.

See spec §Initiative #3 Task 3.3-3.4 and ``Safety gates §1.3`` (prod CRM
mount must never appear in a worker bind list).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from bmad_orchestrator.agent.safety.budget_guard import BudgetGuard, BudgetResult
from bmad_orchestrator.config import BudgetConfig
from bmad_orchestrator.runtime.project_registry import ProjectsRegistry

log = structlog.get_logger(__name__)


# Paths that must NEVER appear as a project root in a multi-run plan.
# Spec §Safety gates §1.3: prod CRM mount must not land in a worker bind list
# because the sandbox topology binds ``slot.path`` read-write into the worker
# tree. Treating these as input-boundary validation keeps the guard cheap and
# loud — registration of a forbidden path raises before any subprocess spawns.
FORBIDDEN_PROJECT_PATHS: tuple[Path, ...] = (
    Path("/home/server/crm"),
)


class MultiRunError(Exception):
    """Base for multi-project run errors."""


class ProjectIsolationError(MultiRunError):
    """Raised when a project path overlaps a forbidden host mount (e.g. prod CRM)."""


# ── data contracts ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ProjectSlot:
    """One project's slice of the multi-run plan."""

    slug: str
    path: Path
    parallel: int


@dataclass(frozen=True)
class MultiProjectPlan:
    """Static input describing one multi-project run."""

    projects: tuple[str, ...]
    total_parallel: int
    wave: str
    per_project_max_stories: int = 50
    daily_max_spend_usd: float = 50.0
    mock: bool = True
    # Review finding P1-B — hard liveness ceiling per child subprocess.
    # Default 4h matches the longest expected real wave; child orchestrator
    # ``--max-stories`` × per-story budget already bounds reasonable cases.
    per_project_timeout_sec: float = 4 * 60 * 60

    def __post_init__(self) -> None:
        if not self.projects:
            raise ValueError("MultiProjectPlan.projects must not be empty")
        if self.total_parallel < 1:
            raise ValueError(
                f"total_parallel must be >= 1, got {self.total_parallel}"
            )
        if len(set(self.projects)) != len(self.projects):
            raise ValueError(f"duplicate slugs in plan: {self.projects}")
        if self.daily_max_spend_usd < 0:
            raise ValueError(
                f"daily_max_spend_usd must be non-negative, got "
                f"{self.daily_max_spend_usd}"
            )
        if self.per_project_timeout_sec <= 0:
            raise ValueError(
                f"per_project_timeout_sec must be positive, got "
                f"{self.per_project_timeout_sec}"
            )


@dataclass(frozen=True)
class ProjectRunResult:
    """Outcome reported by one per-project runner."""

    slug: str
    completed: bool
    spent_usd: float = 0.0
    stories_done: int = 0
    error: str | None = None


@dataclass(frozen=True)
class MultiProjectOutcome:
    """Aggregated outcome across all projects in a multi-run."""

    succeeded: bool
    per_project: dict[str, ProjectRunResult]
    total_spent_usd: float
    aborted_reason: str | None = None


# ── shared budget accumulator ───────────────────────────────────────────────


class SharedSpendTracker:
    """Async-safe accumulator that fronts a shared :class:`BudgetGuard`.

    Every per-project runner calls :meth:`add` after each unit of spend so
    the aggregate daily cap is enforced against the *sum* across projects,
    not per-project independently. The internal :class:`asyncio.Lock`
    serialises concurrent ``add`` calls from gather'd runners so the
    spent_usd snapshot passed to ``enforce_day`` is consistent.

    Read-only access via :attr:`total` is allowed without the lock — it's
    informational and not used for halt decisions.
    """

    def __init__(self, guard: BudgetGuard, *, day_label: str = "today") -> None:
        self._guard = guard
        self._day = day_label
        self._total: float = 0.0
        self._lock = asyncio.Lock()

    async def add(self, amount_usd: float) -> BudgetResult:
        """Increment the aggregate and run ``enforce_day`` on the new total.

        Returns the :class:`BudgetResult` from the shared guard so the
        caller can short-circuit on ``level == 'halt'``.
        """
        if amount_usd < 0:
            raise ValueError(f"amount_usd must be non-negative, got {amount_usd}")
        async with self._lock:
            self._total += amount_usd
            return await self._guard.enforce_day(spent_usd=self._total, day=self._day)

    async def check_only(self) -> BudgetResult:
        """Re-run ``enforce_day`` against current total without adding spend."""
        async with self._lock:
            return await self._guard.enforce_day(spent_usd=self._total, day=self._day)

    @property
    def total(self) -> float:
        return self._total


RunnerFn = Callable[
    [ProjectSlot, SharedSpendTracker, MultiProjectPlan],
    Awaitable[ProjectRunResult],
]


# ── slot allocation ─────────────────────────────────────────────────────────


def split_parallel_slots(
    plan: MultiProjectPlan, registry: ProjectsRegistry
) -> tuple[ProjectSlot, ...]:
    """Distribute ``plan.total_parallel`` workers across ``plan.projects``.

    Allocation is even; the first ``rem = total % N`` projects get one extra
    slot. ``total < N`` is rejected — each project must own at least one
    worker, otherwise nothing useful happens for the unstaffed project.
    Unknown slugs are rejected loudly.
    """
    n = len(plan.projects)
    base, rem = divmod(plan.total_parallel, n)
    if base < 1:
        raise MultiRunError(
            f"total_parallel={plan.total_parallel} < projects={n}; need at "
            f"least one slot per project (use plan.total_parallel >= {n})"
        )
    slots: list[ProjectSlot] = []
    for i, slug in enumerate(plan.projects):
        if slug not in registry.projects:
            raise MultiRunError(
                f"project slug {slug!r} not in registry; "
                f"run `bmad-orchestrator init <path>` first"
            )
        entry = registry.projects[slug]
        parallel = base + (1 if i < rem else 0)
        slots.append(ProjectSlot(slug=slug, path=entry.path, parallel=parallel))
    return tuple(slots)


# ── safety ──────────────────────────────────────────────────────────────────


def validate_project_isolation(slots: tuple[ProjectSlot, ...]) -> None:
    """Refuse if any project path overlaps a forbidden host mount.

    Catches both an exact match (``/home/server/crm``) and any subpath
    (``/home/server/crm/agent``). Prod CRM must never be the root of a
    worker's bind list — the sandbox binds ``slot.path`` read-write and a
    worker would be free to mutate live production files.
    """
    for slot in slots:
        resolved = slot.path.expanduser().resolve()
        for forbidden in FORBIDDEN_PROJECT_PATHS:
            forbidden_resolved = forbidden.expanduser().resolve()
            try:
                resolved.relative_to(forbidden_resolved)
            except ValueError:
                continue
            raise ProjectIsolationError(
                f"project {slot.slug!r} path {resolved} is inside forbidden "
                f"host mount {forbidden_resolved}; prod CRM must never appear "
                f"in a worker bind list (spec §Safety gates §1.3)"
            )


# ── orchestration ───────────────────────────────────────────────────────────


async def run_multi(
    plan: MultiProjectPlan,
    *,
    registry: ProjectsRegistry,
    runner_fn: RunnerFn,
    shared_budget: BudgetGuard | None = None,
    day_label: str = "today",
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> MultiProjectOutcome:
    """Run the orchestrator concurrently across all projects in ``plan``.

    Pipeline:
    1. :func:`split_parallel_slots` → per-project slot allocations.
    2. :func:`validate_project_isolation` → reject prod CRM overlap (L1).
    3. Build / receive :class:`SharedSpendTracker` and pre-flight enforce_day
       at zero spend — if the shared guard is already at ``halt`` (e.g. a
       prior wave drained the daily cap), abort before spawning anything.
    4. ``asyncio.gather`` over runner_fn per slot.
    5. Aggregate results; runner exceptions become per-project errors so one
       failing project does NOT cancel siblings (matches BudgetGuard's
       fail-isolated philosophy).
    """
    slots = split_parallel_slots(plan, registry)
    validate_project_isolation(slots)

    if shared_budget is None:
        cfg = BudgetConfig(daily_limit_usd=plan.daily_max_spend_usd)
        shared_budget = BudgetGuard(cfg)
    tracker = SharedSpendTracker(shared_budget, day_label=day_label)

    pre = await tracker.check_only()
    if pre.level == "halt":
        if on_event:
            on_event({
                "type": "multi_run_aborted",
                "reason": "shared_budget_pre_halt",
                "spent_usd": pre.spent_usd,
                "halt_threshold": pre.halt_threshold,
            })
        return MultiProjectOutcome(
            succeeded=False,
            per_project={},
            total_spent_usd=0.0,
            aborted_reason=(
                f"shared daily budget already at halt: spent="
                f"{pre.spent_usd} cap={pre.halt_threshold}"
            ),
        )

    if on_event:
        on_event({
            "type": "multi_run_starting",
            "projects": [slot.slug for slot in slots],
            "slots": {slot.slug: slot.parallel for slot in slots},
            "total_parallel": plan.total_parallel,
            "daily_max_spend_usd": plan.daily_max_spend_usd,
            "mock": plan.mock,
        })

    async def _wrap(slot: ProjectSlot) -> ProjectRunResult:
        try:
            return await runner_fn(slot, tracker, plan)
        except Exception as exc:
            log.exception("multi_run_project_error", slug=slot.slug)
            return ProjectRunResult(
                slug=slot.slug,
                completed=False,
                error=f"{type(exc).__name__}: {exc}",
            )

    results = await asyncio.gather(*(_wrap(s) for s in slots))
    per = {r.slug: r for r in results}
    total = sum(r.spent_usd for r in results)
    succeeded = all(r.completed for r in results)

    if on_event:
        on_event({
            "type": "multi_run_complete",
            "succeeded": succeeded,
            "per_project": {
                slug: {
                    "completed": r.completed,
                    "spent_usd": r.spent_usd,
                    "stories_done": r.stories_done,
                    "error": r.error,
                }
                for slug, r in per.items()
            },
            "total_spent_usd": total,
            "tracker_total_usd": tracker.total,
        })

    return MultiProjectOutcome(
        succeeded=succeeded,
        per_project=per,
        total_spent_usd=total,
    )


__all__ = [
    "FORBIDDEN_PROJECT_PATHS",
    "MultiProjectOutcome",
    "MultiProjectPlan",
    "MultiRunError",
    "ProjectIsolationError",
    "ProjectRunResult",
    "ProjectSlot",
    "RunnerFn",
    "SharedSpendTracker",
    "run_multi",
    "split_parallel_slots",
    "validate_project_isolation",
]
