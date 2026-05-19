"""Typer CLI per spec §14.1 + §14.4 (3 launch modes) + §16 (model selection).

Команды:
  run                       — запуск оркестратора (foreground / --watch TUI / --daemon)
  status [--live | --watch] — снапшот или TUI dashboard
  pause / resume / stop     — управление
  budget [--wave]           — текущий бюджет
  logs --worker [--tail]    — tail JSONL событий worker'а
  dag --wave                — ASCII DAG
  retro --wave              — ручной retro trigger
  validate-policy           — проверить policy.yaml
  memory --wave             — показать lessons
  model show|set|save       — управление per-role models (§16)
  bot start|stop            — Telegram bot daemon

Запуск (§14.4):
  bmad-orchestrator run --wave 1a --watch          # foreground TUI
  bmad-orchestrator run --wave 1a --daemon         # background daemon
  systemctl --user start bmad-orchestrator@<svc>   # systemd (внешний unit)
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import typer
import yaml
from rich.console import Console
from rich.table import Table

from bmad_orchestrator.agent.run import run_orchestrator
from bmad_orchestrator.cli import models_yaml
from bmad_orchestrator.cli.i18n import t
from bmad_orchestrator.cli.path_validation import (
    ensure_inside_root,
    safe_resolve_path,
)
from bmad_orchestrator.cli.tui import DashboardSnapshot, render_once, run_live
from bmad_orchestrator.config import ModelConfig, Settings, load_settings
from bmad_orchestrator.runtime.multi_run import (
    MultiProjectPlan,
    MultiRunError,
    ProjectIsolationError,
    ProjectRunResult,
    ProjectSlot,
    SharedSpendTracker,
    run_multi,
)
from bmad_orchestrator.runtime.project_registry import (
    ProjectNotFoundError,
    ProjectRegistryError,
    ProjectsRegistry,
    load_registry,
    register_project,
    registry_path,
    resume_hint,
    save_registry,
    scan_registry,
)
from bmad_orchestrator.runtime.project_registry import doctor as run_doctor

app = typer.Typer(
    help="Virgil — autonomous BMad Phase 4 agent (package: bmad-orchestrator)",
    no_args_is_help=True,
)
console = Console()

# Phase 3 eval subcommand group — `bmad-orchestrator eval run`.
eval_app = typer.Typer(
    help="Phase 3 eval suite — run benchmark cases + report metrics",
    no_args_is_help=True,
)
app.add_typer(eval_app, name="eval")

# Phase 5 self-learning subcommand group — `bmad-orchestrator self-learning {run,status,rollback,cron-emit}`.
self_learning_app = typer.Typer(
    help="Self-learning consolidation loop — run, inspect status, rollback, or emit cron trigger",
    no_args_is_help=True,
)
app.add_typer(self_learning_app, name="self-learning")


@eval_app.command("run")
def eval_run(
    case: str | None = typer.Option(
        None,
        "--case",
        help="Run only this case id (e.g. TC-001). Default: all.",
    ),
    mode: str = typer.Option(
        "mock",
        "--mode",
        help="'mock' (default, free) — no real claude binary. 'real' burns tokens.",
    ),
    evals_root: str = typer.Option(
        "evals",
        "--evals-root",
        help="Directory containing cases.yaml + cases/.",
    ),
    fail_under: float = typer.Option(
        0.85,
        "--fail-under",
        help="Exit non-zero if pass_rate falls below this threshold (default 0.85).",
    ),
    repeat: int = typer.Option(
        1,
        "--repeat",
        help="Run each case K times. When K > 1, reports pass@k and pass^k metrics.",
        min=1,
    ),
    metric: str | None = typer.Option(
        None,
        "--metric",
        help=(
            "Metric selector for repeat-mode output. "
            "'pass_caret_k' = production consistency (all k pass). "
            "Default: print both pass@k and pass^k when --repeat > 1."
        ),
    ),
    cases_dir: str | None = typer.Option(
        None,
        "--cases-dir",
        help=(
            "Directory containing a cases.yaml manifest. Overrides "
            "--evals-root/cases.yaml. Used by Phase 3 Step B real-mode runs "
            "(e.g. evals/cases/real/)."
        ),
    ),
    project_root: str | None = typer.Option(
        None,
        "--project-root",
        help=(
            "Path to a real BMad target project. In --mode real, workers "
            "spawn inside this project; ignored in mock mode."
        ),
    ),
    tag: list[str] | None = typer.Option(  # noqa: B008 — typer marker, idiomatic
        None,
        "--tag",
        help=(
            "Filter cases by tag (OR semantics, repeatable). Cases without "
            "the tag field never match."
        ),
    ),
) -> None:
    """Run the eval suite, print a table, save JSON report, exit-code = gate."""
    import time as _time

    from bmad_orchestrator.eval.runner import run_eval_suite_sync, save_report

    evals_root_path = Path(evals_root)
    if not evals_root_path.is_dir():
        console.print(f"[red]evals_root not found: {evals_root_path}[/red]")
        raise typer.Exit(2)

    worktree_root = evals_root_path / ".worktrees-eval"
    worktree_root.mkdir(parents=True, exist_ok=True)

    cases_dir_path = Path(cases_dir) if cases_dir else None
    if cases_dir_path is not None and not cases_dir_path.is_dir():
        console.print(f"[red]--cases-dir not found: {cases_dir_path}[/red]")
        raise typer.Exit(2)

    project_root_path = Path(project_root) if project_root else None
    if project_root_path is not None and not project_root_path.is_dir():
        console.print(f"[red]--project-root not found: {project_root_path}[/red]")
        raise typer.Exit(2)
    if mode == "real" and project_root_path is None:
        console.print("[red]--project-root is required in --mode real[/red]")
        raise typer.Exit(2)

    try:
        results, aggregate, extra_metrics = run_eval_suite_sync(
            evals_root=evals_root_path,
            worktree_root=worktree_root,
            case_filter=case,
            mode=mode,
            repeat=repeat,
            cases_dir=cases_dir_path,
            project_root=project_root_path,
            tags=list(tag) if tag else None,
        )
    except (ValueError, FileNotFoundError) as exc:
        console.print(f"[red]eval failed: {exc}[/red]")
        raise typer.Exit(2) from exc

    table = Table(title=f"Eval suite — {len(results)} cases ({mode} mode, repeat={repeat})")
    table.add_column("id", style="bold")
    table.add_column("level")
    table.add_column("passed")
    table.add_column("verdict")
    table.add_column("iter", justify="right")
    table.add_column("cost $", justify="right")
    table.add_column("latency ms", justify="right")
    for r in results:
        mark = "[green]✓[/green]" if r.passed else "[red]✗[/red]"
        table.add_row(
            r.case_id,
            r.level,
            mark,
            r.final_verdict,
            str(r.review_iteration),
            f"{r.cost_usd:.4f}",
            str(r.latency_ms),
        )
    console.print(table)

    summary = Table(title="Aggregate metrics")
    summary.add_column("metric")
    summary.add_column("value", justify="right")
    summary.add_row("pass_rate", f"{aggregate.pass_rate:.2%}")
    summary.add_row("escalation_rate", f"{aggregate.escalation_rate:.2%}")
    summary.add_row("review_iteration_p95", f"{aggregate.review_iteration_p95:.1f}")
    summary.add_row("cost_per_story_median", f"${aggregate.cost_per_story_median:.4f}")
    summary.add_row("cost_p95", f"${aggregate.cost_p95:.4f}")
    summary.add_row("latency_p95_ms", f"{aggregate.latency_p95_ms:.0f}")

    # Phase 4 hardening #7 — pass^k metrics when --repeat > 1.
    if extra_metrics and repeat > 1:
        k = extra_metrics.get("k", repeat)
        pak = extra_metrics.get("pass_at_k", 0.0)
        pck = extra_metrics.get("pass_caret_k", 0.0)
        show_pak = metric is None or metric == "pass_at_k"
        show_pck = metric is None or metric == "pass_caret_k"
        if show_pak:
            summary.add_row(f"pass@{k}", f"{pak:.2%}")
        if show_pck:
            summary.add_row(f"pass^{k} (consistency)", f"{pck:.2%}")

    console.print(summary)

    # Persist JSON report.
    ts = _time.strftime("%Y%m%d-%H%M%S")
    report_path = evals_root_path / f"results-{ts}.json"
    save_report(results, aggregate, report_path)
    console.print(f"[dim]report: {report_path}[/dim]")

    for r in results:
        if r.failure_reasons:
            console.print(
                f"[yellow]{r.case_id}[/yellow]: " + "; ".join(r.failure_reasons)
            )

    if aggregate.pass_rate < fail_under:
        console.print(
            f"[red]pass_rate {aggregate.pass_rate:.2%} < {fail_under:.2%} — "
            f"Phase 3 gate FAILED[/red]"
        )
        raise typer.Exit(1)
    console.print(
        f"[green]pass_rate {aggregate.pass_rate:.2%} ≥ {fail_under:.2%} — gate OK[/green]"
    )

# Initiative #1 (Task 1.1) — preset parallelism slots for --parallel CLI flag.
# Keep this list narrow: each preset bakes assumptions about memory/CPU caps
# (see Initiative #1 Task 1.3 cgroup work). Adding a value here without a
# matching sandbox preset = silent over-subscription on a busy host.
PARALLEL_PRESETS: tuple[int, ...] = (1, 3, 5, 10)


# H-B (S11 re-review): regex-validate CLI inputs that downstream code embeds
# in shell args, file paths, branch names, or registry lookups. Mirrors the
# registry's `_SLUG_RE` for project slugs; wave/story patterns accept dots so
# operators can pass "1.5" or "Epic1.Story1" without escaping.
_PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_WAVE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_STORY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _validate_cli_token(value: str, *, name: str, pattern: re.Pattern[str]) -> None:
    """Raise typer.BadParameter if value violates the allowed pattern."""
    if not pattern.match(value):
        raise typer.BadParameter(
            f"invalid --{name} {value!r}: must match {pattern.pattern}"
        )


# ── helpers ──────────────────────────────────────────────────────────────────


def _resolve_models(
    *,
    model: str | None,
    planner_model: str | None,
    reviewer_model: str | None,
    dev_model: str | None,
    routine_model: str | None,
    mechanical_model: str | None,
    fallback_model: str | None,
) -> ModelConfig:
    """Compose ModelConfig honoring per-project YAML + CLI overrides per §16."""
    settings = load_settings()
    project_yaml = models_yaml.config_path(settings.target_project)
    cfg = models_yaml.load_models(project_yaml)
    cfg = models_yaml.apply_all(cfg, model)
    return models_yaml.apply_overrides(
        cfg,
        planner=planner_model,
        reviewer=reviewer_model,
        dev=dev_model,
        routine=routine_model,
        mechanical=mechanical_model,
        fallback=fallback_model,
    )


def _build_snapshot(
    *, project: str | None = None, wave: str | None = None
) -> DashboardSnapshot:
    """Best-effort dashboard snapshot read from live sources.

    Sources, each isolated (broken/missing → empty defaults, no exception):
      - state.db        → status / wave / project (latest active session) + day spend
      - sprint-status   → progress + ready_next + done_recent
      - events.jsonl    → active workers + tail events

    `project` / `wave` arguments override what's read from state.db (used by
    `status --project X --wave Y` to label the snapshot when no session is
    active yet).
    """
    from bmad_orchestrator.cli.snapshot import (
        derive_agent_thinking,
        derive_budget_level,
        read_active_workers,
        read_events_tail,
        read_latest_session,
        read_session_budget,
        read_sprint_progress,
    )

    settings = load_settings()
    snap = DashboardSnapshot(
        status="idle",
        wave=wave,
        project=project or settings.target_project.name,
        budget_cap_usd=settings.budget.daily_limit_usd,
        agent_thinking=t("agent.idle"),
    )

    try:
        session = read_latest_session(settings.state_db)
    except Exception:
        session = None
    if session is not None:
        snap.status = session.status
        snap.wave = wave or session.wave
        snap.project = project or session.target_project
        try:
            snap.budget_spent_usd = read_session_budget(settings.state_db, session.id)
        except Exception:
            snap.budget_spent_usd = 0.0
        snap.budget_level = derive_budget_level(
            snap.budget_spent_usd, snap.budget_cap_usd
        )

    # File-based sources (sprint-status, events.jsonl) live under whatever
    # project the *session* points at, which may not match the env-bound
    # `settings.target_project`. Resolve through the project registry so
    # `virgil run --project antares` shows antares' progress even when env
    # still says odyssey (the active --project-vs-env bug).
    effective = _resolve_settings_for_project(settings, snap.project)

    try:
        progress = read_sprint_progress(effective)
    except Exception:
        progress = None
    if progress is not None:
        snap.progress_done = progress.done
        snap.progress_total = progress.total
        snap.ready_next = progress.ready_next
        snap.done_recent = progress.done_recent

    if snap.wave:
        try:
            snap.workers = read_active_workers(effective, snap.wave)
        except Exception:
            snap.workers = []
        try:
            snap.events_tail = read_events_tail(effective, snap.wave)
        except Exception:
            snap.events_tail = []

    snap.agent_thinking = derive_agent_thinking(snap.status, snap.workers)
    return snap


def _resolve_settings_for_project(
    settings: Settings, project_slug: str | None, *, strict: bool = False
) -> Settings:
    """Return Settings with target_project pointed at `project_slug`'s path.

    CLI precedence (NEW-1) — the resolved target project is decided in this
    order, highest wins:

        1. ``--project <slug>`` flag  (this function, via the registry)
        2. ``config/projects.yaml`` registry lookup
        3. ``ORCHESTRATOR_TARGET_PROJECT`` env var  (bound onto Settings)
        4. ``Settings`` default (``/home/server/odyssey``)

    So an explicit ``--project`` deterministically beats the env var: when the
    slug resolves through the registry, the env-bound ``settings.target_project``
    is replaced with the registry path.

    `strict` controls the registry-miss behaviour:

      - ``strict=False`` (default, used by read-only status snapshots): an
        absent registry entry or an unreadable registry degrades gracefully —
        the original env-bound `settings` is returned. A wrong-but-non-crashing
        snapshot is acceptable for a display path.
      - ``strict=True`` (used by `virgil run` and other mutating subcommands):
        an absent entry raises :class:`ProjectNotFoundError` and a malformed
        registry propagates its load error. Silent degrade here would spawn
        worktrees in the wrong project — the NEW-1 bug.

    Falls back to the original `settings` without consulting the registry when
    `project_slug` is None or already matches `settings.target_project.name`.
    """
    if not project_slug or project_slug == settings.target_project.name:
        return settings
    reg_path = registry_path(orchestrator_home=settings.orchestrator_home)
    try:
        reg = load_registry(reg_path)
    except Exception:
        if strict:
            raise
        return settings
    entry = reg.projects.get(project_slug)
    if entry is None:
        if strict:
            raise ProjectNotFoundError(
                f"Project '{project_slug}' not found in {reg_path}. "
                f"Run 'virgil project add' or check config/projects.yaml."
            )
        return settings
    if entry.path != settings.target_project:
        console.print(
            f"[dim]overriding ORCHESTRATOR_TARGET_PROJECT="
            f"{settings.target_project} with --project={project_slug} "
            f"→ resolved path {entry.path}[/dim]"
        )
    return settings.model_copy(update={"target_project": entry.path})


# ── run ──────────────────────────────────────────────────────────────────────


@app.command()
def run(
    project: str = typer.Option(..., "--project", help="Target project name"),
    wave: str = typer.Option(..., "--wave", help="Wave identifier (e.g. 1a)"),
    max_parallel: int = typer.Option(3, "--max-parallel"),
    parallel: int | None = typer.Option(
        None, "--parallel",
        help=(
            "Preset N parallel workers (1, 3, 5, 10). Overrides --max-parallel "
            "if set. See Initiative #1 Task 1.1."
        ),
    ),
    model: str | None = typer.Option(None, "--model", help="Set all roles to one model"),
    planner_model: str | None = typer.Option(None, "--planner-model"),
    reviewer_model: str | None = typer.Option(None, "--reviewer-model"),
    dev_model: str | None = typer.Option(None, "--dev-model"),
    routine_model: str | None = typer.Option(None, "--routine-model"),
    mechanical_model: str | None = typer.Option(None, "--mechanical-model"),
    fallback_model: str | None = typer.Option(None, "--fallback-model"),
    watch: bool = typer.Option(False, "--watch", help="Foreground TUI (§14.4)"),
    daemon: bool = typer.Option(False, "--daemon", help="Background daemon (§14.4)"),
    mock: bool = typer.Option(
        True, "--mock/--real",
        help=(
            "Mock-mode E2E pilot — no real spawns. Default ON (N3 FS6); "
            "use --real for real-mode. Real-mode runs on Claude subscription "
            "(claude -p CLI); ANTHROPIC_API_KEY is only needed for the bot's "
            "NL intent-router (slash-commands work without it)."
        ),
    ),
    max_stories: int = typer.Option(
        50, "--max-stories",
        help="Hard cap на число story spawns в этом запуске (default 50)",
    ),
    max_spend_usd: float = typer.Option(
        50.0, "--max-spend-usd",
        help="Soft cap на дневной spend в USD (default 50.0)",
    ),
    story: list[str] = typer.Option(  # noqa: B008 — typer pattern
        [], "--story",
        help=(
            "Запустить только эту story (можно повторить --story). "
            "Bypass'ит DAG planner — оператор отвечает за dependency-correctness. "
            "Полезно для smoke pilot на одной known-ready story."
        ),
    ),
) -> None:
    """Запустить оркестратор на указанной wave."""
    _validate_cli_token(project, name="project", pattern=_PROJECT_RE)
    _validate_cli_token(wave, name="wave", pattern=_WAVE_RE)
    for sid in story:
        _validate_cli_token(sid, name="story", pattern=_STORY_RE)
    if parallel is not None:
        if parallel not in PARALLEL_PRESETS:
            raise typer.BadParameter(
                f"--parallel must be one of: {', '.join(str(p) for p in PARALLEL_PRESETS)}"
            )
        max_parallel = parallel

    # NEW-1 — `--project` must beat the `ORCHESTRATOR_TARGET_PROJECT` env var.
    # Resolve the slug through the registry in strict mode (unregistered slug
    # → fail loud) and hand the resolved Settings to `run_orchestrator` so it
    # spawns worktrees in the requested project, not the env-bound default.
    base_settings = load_settings()
    try:
        effective_settings = _resolve_settings_for_project(
            base_settings, project, strict=True
        )
    except ProjectNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    models_cfg = _resolve_models(
        model=model,
        planner_model=planner_model,
        reviewer_model=reviewer_model,
        dev_model=dev_model,
        routine_model=routine_model,
        mechanical_model=mechanical_model,
        fallback_model=fallback_model,
    )

    if daemon:
        # Spawn ourselves detached (§14.4 mode 2).
        # --parallel already collapsed into max_parallel above; pass only the
        # underlying integer so the daemon child reproduces the chosen slot
        # count without re-validating the preset.
        args = [sys.executable, "-m", "bmad_orchestrator.cli", "run",
                "--project", project, "--wave", wave,
                "--max-parallel", str(max_parallel),
                "--max-stories", str(max_stories),
                "--max-spend-usd", str(max_spend_usd)]
        for sid in story:
            args.extend(["--story", sid])
        if mock:
            args.append("--mock")
        else:
            args.append("--real")
        env = dict(os.environ)
        env["ORCHESTRATOR_DAEMON"] = "1"
        proc = subprocess.Popen(  # noqa: S603 — own args, no shell  # nosec
            args,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        console.print(f"[green]daemon pid={proc.pid}[/green] — wave {wave}")
        return

    console.print(
        f"[bold cyan]Virgil[/bold cyan] [cyan]starting[/cyan] "
        f"project={project} wave={wave} "
        f"max_parallel={max_parallel} mock={mock}",
    )
    console.print(
        f"[dim]models: planner={models_cfg.planner} reviewer={models_cfg.reviewer} "
        f"dev={models_cfg.dev} routine={models_cfg.routine} "
        f"mechanical={models_cfg.mechanical} fallback={models_cfg.fallback}[/dim]",
    )

    if watch:
        # Launch run in background asyncio task while TUI refreshes.
        async def _runner() -> None:
            await run_orchestrator(
                project=project, wave=wave, max_parallel=max_parallel,
                models=models_cfg, mock=mock,
                max_stories=max_stories, max_spend_usd=max_spend_usd,
                stories=tuple(story) if story else None,
                settings=effective_settings,
            )

        async def _supervised() -> None:
            task = asyncio.create_task(_runner())
            try:
                # TUI runs in main thread; orchestrator in task.
                run_live(lambda: _build_snapshot(project=project, wave=wave),
                         refresh_per_second=0.5, iterations=1)
            finally:
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

        asyncio.run(_supervised())
        return

    asyncio.run(
        run_orchestrator(
            project=project, wave=wave, max_parallel=max_parallel,
            models=models_cfg, mock=mock,
            max_stories=max_stories, max_spend_usd=max_spend_usd,
            stories=tuple(story) if story else None,
            settings=effective_settings,
        )
    )


# ── observability ────────────────────────────────────────────────────────────


@app.command()
def status(
    live: bool = typer.Option(False, "--live", help="Streaming TUI refresh"),
    watch: bool = typer.Option(False, "--watch", help="Alias for --live"),
    project: str | None = typer.Option(None, "--project"),
    wave: str | None = typer.Option(None, "--wave"),
    iterations: int | None = typer.Option(None, "--iterations", hidden=True),
) -> None:
    """Снимок состояния или live TUI (§14.2)."""
    if project is not None:
        _validate_cli_token(project, name="project", pattern=_PROJECT_RE)
    if wave is not None:
        _validate_cli_token(wave, name="wave", pattern=_WAVE_RE)
    if live or watch:
        run_live(
            lambda: _build_snapshot(project=project, wave=wave),
            refresh_per_second=0.5,
            iterations=iterations,
        )
        return
    snap = _build_snapshot(project=project, wave=wave)
    console.print(render_once(snap), markup=False, highlight=False)


@app.command()
def pause() -> None:
    """Приостановить оркестратора (graceful)."""
    console.print(f"[cyan]→[/cyan] {t('agent.pause_requested')}")


@app.command()
def resume() -> None:
    """Продолжить после паузы."""
    console.print(f"[cyan]→[/cyan] {t('agent.resume_requested')}")


@app.command()
def stop(graceful: bool = typer.Option(True, "--graceful/--hard")) -> None:
    """Остановить оркестратора."""
    mode = "graceful" if graceful else "hard"
    console.print(f"[cyan]→[/cyan] stop mode={mode}")


@app.command()
def menu() -> None:
    """Запустить интерактивное меню (стрелки/Enter — без CLI-флагов)."""
    from bmad_orchestrator.cli.menu import launch_menu

    launch_menu(app)


@app.command()
def budget(wave: str | None = typer.Option(None, "--wave")) -> None:
    """Показать текущий бюджет."""
    if wave is not None:
        _validate_cli_token(wave, name="wave", pattern=_WAVE_RE)
    settings = load_settings()
    table = Table(title=f"Budget ({wave or 'overall'})", show_header=True)
    table.add_column("scope")
    table.add_column("alarm $", justify="right")
    table.add_column("halt $", justify="right")
    table.add_row("story", f"{settings.budget.story_alarm_usd:.2f}",
                  f"{settings.budget.story_halt_usd:.2f}")
    table.add_row("batch", f"{settings.budget.batch_alarm_usd:.2f}",
                  f"{settings.budget.batch_halt_usd:.2f}")
    table.add_row("daily", "—", f"{settings.budget.daily_limit_usd:.2f}")
    console.print(table)


@app.command()
def logs(
    worker: str = typer.Option(..., "--worker"),
    tail: int = typer.Option(50, "--tail"),
) -> None:
    """Tail JSONL событий worker'а."""
    # Worker id flows into ``rglob(f"*{worker}*.jsonl")`` — unvalidated glob
    # metachars (``**``, ``?``, ``[abc]``) would trigger a full-tree walk.
    _validate_cli_token(worker, name="worker", pattern=_STORY_RE)
    settings = load_settings()
    runs = settings.target_project / "_bmad-output" / "runs"
    candidates = sorted(runs.rglob(f"*{worker}*.jsonl"), reverse=True)
    if not candidates:
        console.print(f"[yellow]no JSONL for worker {worker}[/yellow]")
        raise typer.Exit(code=1)
    path = candidates[0]
    lines = path.read_text(encoding="utf-8").splitlines()[-tail:]
    for line in lines:
        console.print(line)


@app.command()
def dag(wave: str = typer.Option(..., "--wave")) -> None:
    """Показать DAG в ASCII."""
    _validate_cli_token(wave, name="wave", pattern=_WAVE_RE)
    from bmad_orchestrator.runtime.dag_planner import DagPlanner

    planner = DagPlanner.from_target()
    sprint_wave = str(planner.sprint_status.get("wave", ""))
    if sprint_wave and sprint_wave != wave:
        console.print(f"[yellow]wave mismatch:[/yellow] sprint says {sprint_wave}, you asked {wave}")
    ready = planner.find_ready(max_n=20)
    if not ready:
        console.print("[dim]no ready stories[/dim]")
        return
    table = Table(title=f"Wave {wave} — ready stories", show_header=True)
    table.add_column("id")
    table.add_column("epic")
    table.add_column("risk")
    table.add_column("touches")
    for s in ready:
        table.add_row(
            str(s.get("id", "?")),
            str(s.get("epic_id", "?")),
            str(s.get("risk", "?")),
            ", ".join(s.get("touches_files", []) or []),
        )
    console.print(table)


@app.command()
def retro(wave: str = typer.Option(..., "--wave")) -> None:
    """Запустить retrospective вручную."""
    _validate_cli_token(wave, name="wave", pattern=_WAVE_RE)
    console.print(f"[cyan]→[/cyan] retro wave={wave} (will use bmad-retrospective skill)")


_DEFAULT_POLICY_PATH = Path("examples/elicitation-policy.example.yaml")


@app.command(name="validate-policy")
def validate_policy(
    path: Path = typer.Option(_DEFAULT_POLICY_PATH, "--path"),  # noqa: B008 — typer pattern
) -> None:
    """Проверить elicitation-policy.yaml на синтаксис + базовые поля."""
    if not path.is_file():
        console.print(f"[red]policy not found:[/red] {path}")
        raise typer.Exit(code=1)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        console.print(f"[red]invalid YAML:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    if not isinstance(data, dict):
        console.print("[red]policy must be a mapping[/red]")
        raise typer.Exit(code=3)
    rules = data.get("rules") or []
    console.print(f"[green]ok[/green] {len(rules)} rules in {path}")


@app.command()
def memory(wave: str = typer.Option(..., "--wave")) -> None:
    """Показать lessons из wave."""
    _validate_cli_token(wave, name="wave", pattern=_WAVE_RE)
    settings = load_settings()
    mem = settings.orchestrator_home / ".claude" / "memory" / "per-wave" / f"{wave}-retrospective.md"
    if not mem.is_file():
        console.print(f"[yellow]no retro for wave {wave}[/yellow] ({mem})")
        raise typer.Exit(code=1)
    console.print(mem.read_text(encoding="utf-8"))


# ── model ────────────────────────────────────────────────────────────────────


model_app = typer.Typer(help="Per-role model configuration (§16)")
app.add_typer(model_app, name="model")


@model_app.command("show")
def model_show() -> None:
    """Показать текущую раскладку моделей."""
    settings = load_settings()
    cfg = models_yaml.load_models(models_yaml.config_path(settings.target_project))
    table = Table(title="Per-role models", show_header=True)
    table.add_column("role", style="bold")
    table.add_column("model")
    for role in models_yaml.ROLE_FIELDS:
        table.add_row(role, str(getattr(cfg, role)))
    console.print(table)


@model_app.command("set")
def model_set(
    role: str = typer.Argument(..., help="Role name or 'all'"),
    value: str = typer.Argument(..., help="Model id (e.g. claude-opus-4-7)"),
    save: bool = typer.Option(False, "--save", help="Persist to YAML"),
) -> None:
    """Установить модель для роли (или для всех с role=all)."""
    settings = load_settings()
    path = models_yaml.config_path(settings.target_project)
    cfg = models_yaml.load_models(path)
    if role == "all":
        cfg = models_yaml.apply_all(cfg, value)
    else:
        cfg = models_yaml.apply_overrides(cfg, **{role: value})
    console.print(f"[green]ok[/green] {role} → {value}")
    if save:
        models_yaml.save_models(path, cfg)
        console.print(f"[dim]saved → {path}[/dim]")


@model_app.command("save")
def model_save() -> None:
    """Записать текущие defaults в YAML target проекта."""
    settings = load_settings()
    path = models_yaml.config_path(settings.target_project)
    models_yaml.save_models(path, settings.models)
    console.print(f"[green]saved[/green] {path}")


# ── multi-project registry (Initiative #3 Task 3.1-3.2) ──────────────────────


def _load_registry_for_cli() -> tuple[Path, ProjectsRegistry]:
    settings = load_settings()
    path = registry_path(orchestrator_home=settings.orchestrator_home)
    try:
        reg = load_registry(path)
    except Exception as exc:
        console.print(f"[red]registry load failed[/red] {path}: {exc}")
        raise typer.Exit(code=2) from exc
    return path, reg


@app.command()
def init(
    project_path: Path = typer.Argument(  # noqa: B008 — typer pattern
        ..., help="Absolute path to the BMad project to register"
    ),
    slug: str | None = typer.Option(
        None, "--slug", help="Override auto-derived slug (default = basename)"
    ),
) -> None:
    """Register a project in the multi-project registry (Init #3 Task 3.1)."""
    path, reg = _load_registry_for_cli()
    try:
        new_reg, final_slug, entry = register_project(reg, project_path, slug=slug)
    except ProjectRegistryError as exc:
        console.print(f"[red]init failed[/red]: {exc}")
        raise typer.Exit(code=2) from exc
    save_registry(new_reg, path)
    console.print(
        f"[green]registered[/green] {final_slug} → {entry.path} "
        f"(layout={entry.bmad_layout}) at {path}"
    )


@app.command()
def scan() -> None:
    """List all known projects with on-disk status (Init #3 Task 3.2)."""
    _, reg = _load_registry_for_cli()
    rows = scan_registry(reg)
    if not rows:
        console.print("[dim]registry empty — run `bmad-orchestrator init <path>` first[/dim]")
        return
    table = Table(title="Known projects", show_header=True)
    table.add_column("slug", style="bold")
    table.add_column("layout")
    table.add_column("status")
    table.add_column("path")
    table.add_column("detail")
    for row in rows:
        colour = {"ok": "green", "stale": "yellow", "missing": "red"}[row.status]
        table.add_row(
            row.slug,
            row.bmad_layout,
            f"[{colour}]{row.status}[/{colour}]",
            str(row.path),
            row.detail or "",
        )
    console.print(table)


@app.command()
def doctor(
    project: str = typer.Argument(..., help="Project slug (from `scan`)"),
) -> None:
    """Run health checks on one project (Init #3 Task 3.2)."""
    _validate_cli_token(project, name="project", pattern=_PROJECT_RE)
    _, reg = _load_registry_for_cli()
    report = run_doctor(reg, project)
    table = Table(title=f"doctor {project}", show_header=True)
    table.add_column("check", style="bold")
    table.add_column("ok")
    table.add_column("detail")
    for check in report.checks:
        table.add_row(
            check.name,
            "[green]ok[/green]" if check.ok else "[red]fail[/red]",
            check.detail,
        )
    console.print(table)
    if not report.healthy:
        raise typer.Exit(code=1)


@app.command(name="resume-project")
def resume_project(
    project: str = typer.Argument(..., help="Project slug to resume"),
) -> None:
    """Print a shell hint to resume work on a project (Init #3 Task 3.2).

    Named ``resume-project`` because the top-level ``resume`` verb is already
    bound to the orchestrator pause/resume daemon control.
    """
    _validate_cli_token(project, name="project", pattern=_PROJECT_RE)
    _, reg = _load_registry_for_cli()
    console.print(resume_hint(reg, project))


# ── multi-project run (Init #3 Task 3.3-3.4) ─────────────────────────────────


# Review finding P1-C — bound child stderr in memory. Real wave can run for hours
# and a chatty sub-agent can emit MBs of warnings; ``proc.communicate()`` keeps
# every byte in RAM and we ship the tail to the result anyway. Cap = 64 KiB —
# tail is what matters for error context.
_SUBPROCESS_STDERR_CAP_BYTES = 64 * 1024


# Review finding P1-D — env allow-list for child subprocess. Mirror of
# ``runtime.sandbox._SANDBOX_DEFAULT_ENV_ALLOWLIST`` discipline: never inherit
# orchestrator-internal env (``BMAD_DISABLE_BUDGET``, ``BMAD_AUTO_SPLIT``,
# ``BMAD_PROJECTS_REGISTRY``, ``BMAD_REQUIRE_CGROUP``…) into a project worker
# so a parent-shell flag cannot silently disable the very gates ``multi`` was
# added to enforce. The only ``BMAD_*`` overrides allowed are the ones this
# module *explicitly* sets per slot — see ``_subprocess_env``.
_SUBPROCESS_ENV_ALLOWLIST: frozenset[str] = frozenset({
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "TERM",
    "SHELL",
    "ANTHROPIC_API_KEY",
    "CLAUDE_API_KEY",
    "XDG_RUNTIME_DIR",
    "XDG_CACHE_HOME",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "TMPDIR",
})


def _subprocess_env(slot_path: Path, spend_report: Path) -> dict[str, str]:
    """Build the env for a per-project subprocess from a strict allow-list.

    Per review finding P1-D: orchestrator-internal env (``BMAD_*``,
    ``ORCHESTRATOR_*``) is *not* propagated. The two slot-scoped variables
    the child genuinely needs (``ORCHESTRATOR_TARGET_PROJECT`` and
    ``BMAD_MULTI_SPEND_REPORT``) are appended explicitly.
    """
    env: dict[str, str] = {
        key: value
        for key, value in os.environ.items()
        if key in _SUBPROCESS_ENV_ALLOWLIST
    }
    env["ORCHESTRATOR_TARGET_PROJECT"] = str(slot_path)
    env["BMAD_MULTI_SPEND_REPORT"] = str(spend_report)
    return env


async def _drain_capped(
    stream: asyncio.StreamReader | None, cap_bytes: int
) -> bytes:
    """Drain ``stream`` to EOF, keeping only the first ``cap_bytes``.

    Continues reading past the cap so the child's stderr PIPE never fills and
    blocks ``proc.wait()``; the overflow is discarded. Returns the captured
    head as bytes.
    """
    if stream is None:
        return b""
    buf = bytearray()
    while True:
        chunk = await stream.read(8192)
        if not chunk:
            break
        if len(buf) < cap_bytes:
            buf.extend(chunk[: cap_bytes - len(buf)])
    return bytes(buf)


async def _subprocess_runner(
    slot: ProjectSlot,
    tracker: SharedSpendTracker,
    plan: MultiProjectPlan,
) -> ProjectRunResult:
    """Real runner — spawns ``bmad-orchestrator run --project <slug>`` per slot.

    Per-project isolation = subprocess env: ``ORCHESTRATOR_TARGET_PROJECT``
    is set to the slot's path so every ``load_settings()`` inside the child
    resolves to that project's tree. State.db rows, project memory files,
    and sprint-status writes therefore never cross project boundaries.

    The subprocess returncode is the completion signal. Final per-project
    spend is read from a child-written ``spend.json`` (review finding P1-A:
    parent <-> child handoff via filesystem since stdout is discarded), then
    folded into the shared :class:`SharedSpendTracker` so the daily cap halts
    subsequent waves once the aggregate is reached.
    """
    spend_report = Path(
        tempfile.mkdtemp(prefix=f"bmad-multi-{slot.slug}-")
    ) / "spend.json"
    env = _subprocess_env(slot.path, spend_report)
    args = [
        sys.executable, "-m", "bmad_orchestrator.cli", "run",
        "--project", slot.slug,
        "--wave", plan.wave,
        "--max-parallel", str(slot.parallel),
        "--max-stories", str(plan.per_project_max_stories),
        "--max-spend-usd",
        str(plan.daily_max_spend_usd / max(len(plan.projects), 1)),
    ]
    args.append("--mock" if plan.mock else "--real")
    proc = await asyncio.create_subprocess_exec(
        *args,
        env=env,
        stdin=asyncio.subprocess.DEVNULL,
        # Review finding P1-C — orchestrator stdout is chatty (per-story logs,
        # cost ticks). Discard at OS level so PIPE never fills and we don't
        # buffer MBs of text we won't use.
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    # Review finding P1-B — hard timeout. A hung child (auth prompt, network
    # deadlock, runaway sub-agent loop) must not park the entire multi-run
    # forever. On expiry: SIGTERM → 30s grace → SIGKILL; surface as failed
    # ProjectRunResult so siblings continue.
    timed_out = False
    stderr = b""

    async def _wait_and_drain() -> bytes:
        captured, _ = await asyncio.gather(
            _drain_capped(proc.stderr, _SUBPROCESS_STDERR_CAP_BYTES),
            proc.wait(),
        )
        return captured

    try:
        stderr = await asyncio.wait_for(
            _wait_and_drain(),
            timeout=plan.per_project_timeout_sec,
        )
    except TimeoutError:
        timed_out = True
        try:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=30)
            except TimeoutError:
                proc.kill()
                await proc.wait()
        except ProcessLookupError:
            pass
    spent_usd = _read_spend_report(spend_report)
    if spent_usd > 0:
        await tracker.add(spent_usd)
    completed = (not timed_out) and proc.returncode == 0
    if timed_out:
        err = f"timeout after {plan.per_project_timeout_sec}s"
    elif completed:
        err = None
    else:
        err = (
            f"exit {proc.returncode}: "
            f"{stderr.decode('utf-8', errors='replace')[:400]}"
        )
    return ProjectRunResult(
        slug=slot.slug,
        completed=completed,
        spent_usd=spent_usd,
        error=err,
    )


def _read_spend_report(path: Path) -> float:
    """Read ``spend.json`` written by a child orchestrator (P1-A handoff).

    Returns the reported ``spent_usd`` on success, ``0.0`` on missing or
    malformed file. Best-effort cleanup of the tempdir; we never raise from
    a parsing error because the subprocess returncode is the authoritative
    completion signal — failed cost telemetry should not mask a real exit.
    """
    try:
        if not path.exists():
            return 0.0
        payload = json.loads(path.read_text(encoding="utf-8"))
        return float(payload.get("spent_usd", 0.0))
    except (OSError, ValueError, TypeError):
        return 0.0
    finally:
        try:
            if path.exists():
                path.unlink()
            if path.parent.exists() and path.parent.name.startswith(
                "bmad-multi-"
            ):
                path.parent.rmdir()
        except OSError:
            pass


@app.command()
def multi(
    projects: str = typer.Option(
        ..., "--projects",
        help="Comma-separated registry slugs (e.g. antares,odyssey)",
    ),
    wave: str = typer.Option(..., "--wave"),
    parallel: int = typer.Option(
        10, "--parallel",
        help="Total worker slots across all projects (split evenly)",
    ),
    max_stories: int = typer.Option(
        50, "--max-stories", help="Per-project hard cap on story spawns",
    ),
    daily_max_spend_usd: float = typer.Option(
        50.0, "--daily-max-spend-usd",
        help="Shared daily USD cap across all projects (single guard)",
    ),
    mock: bool = typer.Option(True, "--mock/--real"),
) -> None:
    """Запустить оркестратор одновременно над несколькими проектами (Init #3 Task 3.3-3.4)."""
    _validate_cli_token(wave, name="wave", pattern=_WAVE_RE)
    slugs = tuple(s.strip() for s in projects.split(",") if s.strip())
    if not slugs:
        raise typer.BadParameter("--projects must list at least one slug")
    for slug in slugs:
        _validate_cli_token(slug, name="projects", pattern=_PROJECT_RE)

    plan = MultiProjectPlan(
        projects=slugs,
        total_parallel=parallel,
        wave=wave,
        per_project_max_stories=max_stories,
        daily_max_spend_usd=daily_max_spend_usd,
        mock=mock,
    )
    _, reg = _load_registry_for_cli()

    try:
        outcome = asyncio.run(
            run_multi(plan, registry=reg, runner_fn=_subprocess_runner)
        )
    except (MultiRunError, ProjectIsolationError) as exc:
        raise typer.BadParameter(str(exc)) from exc

    verdict = "[green]OK[/green]" if outcome.succeeded else "[red]FAIL[/red]"
    console.print(
        f"[bold]Multi-project run {verdict}[/bold] — "
        f"projects={len(outcome.per_project)} total_spent=${outcome.total_spent_usd:.2f}"
    )
    if outcome.aborted_reason:
        console.print(f"[red]aborted: {outcome.aborted_reason}[/red]")
    table = Table(show_header=True)
    table.add_column("project")
    table.add_column("done")
    table.add_column("spent_usd", justify="right")
    table.add_column("stories", justify="right")
    table.add_column("error", overflow="fold")
    for slug in plan.projects:
        r = outcome.per_project.get(slug)
        if r is None:
            table.add_row(slug, "—", "—", "—", "not run")
            continue
        table.add_row(
            slug,
            "✓" if r.completed else "✗",
            f"{r.spent_usd:.2f}",
            str(r.stories_done),
            r.error or "",
        )
    console.print(table)
    if not outcome.succeeded:
        raise typer.Exit(code=1)


# ── skill upgrade pipeline (§4 E4) ───────────────────────────────────────────


_DEFAULT_SKILLS_ROOT = Path(__file__).resolve().parents[3] / "skills"


def _skill_status_table(snapshot: dict[str, object]) -> Table:
    version_raw = snapshot.get("version")
    patches_raw = snapshot.get("patches") or []
    pending_raw = snapshot.get("pending_conflicts") or []
    patches: list[str] = [str(p) for p in patches_raw] if isinstance(patches_raw, list) else []
    pending: list[str] = [str(p) for p in pending_raw] if isinstance(pending_raw, list) else []
    table = Table(title="Virgil — skills", show_header=True)
    table.add_column("field", style="bold")
    table.add_column("value")
    if isinstance(version_raw, dict):
        table.add_row("source_repo", str(version_raw.get("source_repo", "?")))
        table.add_row("source_git_rev", str(version_raw.get("source_git_rev", "?")))
        table.add_row("source_git_date", str(version_raw.get("source_git_date", "?")))
        table.add_row("copied_at", str(version_raw.get("copied_at", "?")))
        table.add_row("skills_count", str(version_raw.get("skills_count", "?")))
    else:
        table.add_row("version", "[red]missing[/red] (run skill-update)")
    table.add_row("patches", ", ".join(patches) if patches else "(none)")
    table.add_row(
        "pending_conflicts",
        ", ".join(pending) if pending else "(none)",
    )
    return table


@app.command(name="skill-update")
def skill_update(
    source: Path | None = typer.Option(  # noqa: B008 — typer pattern
        None,
        "--source",
        help=(
            "Override upstream source path. Defaults to source_path "
            "recorded in skills/upstream/.bmad-version."
        ),
    ),
    skills_root: Path = typer.Option(  # noqa: B008 — typer pattern
        _DEFAULT_SKILLS_ROOT,
        "--skills-root",
        help="Root of orchestrator skills dir (default: <repo>/skills).",
    ),
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Apply changes to disk. Default is dry-run (no writes).",
    ),
) -> None:
    """Pull upstream BMad skills, diff, re-apply patches.

    Default: dry-run — prints summary, never writes. Use ``--apply`` to swap
    the on-disk ``skills/upstream/`` tree and update ``.bmad-version``.
    ``skills/customize/``, ``skills/policy/``, ``skills/lessons/`` are never
    touched.
    """
    skills_root = safe_resolve_path(skills_root, name="--skills-root")

    from bmad_orchestrator.runtime.skill_update import (
        BmadVersionInvalidError,
        BmadVersionNotFoundError,
        PatchConflictError,
        SourceMissingError,
        update_skills,
    )

    try:
        result = update_skills(
            skills_root=skills_root,
            source=source,
            dry_run=not apply,
        )
    except (BmadVersionNotFoundError, BmadVersionInvalidError) as exc:
        console.print(f"[red]bad .bmad-version:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    except SourceMissingError as exc:
        console.print(f"[red]source missing:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    except PatchConflictError as exc:
        console.print(f"[red]patch conflict during apply:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    diff = result.diff
    console.print(
        f"[cyan]source:[/cyan] {result.source_path}  "
        f"[cyan]rev:[/cyan] {result.source_git_rev or '(unknown)'}"
    )
    console.print(
        f"[cyan]diff:[/cyan] +{len(diff.added)} / "
        f"~{len(diff.modified)} / -{len(diff.removed)}"
    )
    conflicts = result.conflicts
    if conflicts:
        console.print(
            f"[red]patch conflicts:[/red] {len(conflicts)} "
            f"(report: {result.conflict_report})"
        )
        for r in conflicts:
            console.print(f"  - {r.patch_name}: {r.detail or '(no detail)'}")
        raise typer.Exit(code=1)
    if result.patch_results:
        console.print(f"[green]patches ok:[/green] {len(result.patch_results)}")
    if result.applied:
        console.print("[green]applied[/green] — .bmad-version updated")
    else:
        console.print("[dim]dry-run — no writes. Re-run with --apply to commit.[/dim]")


@app.command(name="policy-apply")
def policy_apply(
    project: str = typer.Argument(..., help="Project slug (e.g. odyssey)"),
    auto_apply: bool = typer.Option(
        False,
        "--auto-apply",
        help="Apply every proposal without prompting (CI / scripted use).",
    ),
    lessons_dir: Path | None = typer.Option(  # noqa: B008 — typer pattern
        None,
        "--lessons-dir",
        help=(
            "Override lessons directory. Defaults to "
            "<skills_root>/lessons/<project>/."
        ),
    ),
    skills_root: Path = typer.Option(  # noqa: B008 — typer pattern
        _DEFAULT_SKILLS_ROOT,
        "--skills-root",
        help="Root of orchestrator skills dir (default: <repo>/skills).",
    ),
    orchestrator_home: Path | None = typer.Option(  # noqa: B008 — typer pattern
        None,
        "--orchestrator-home",
        help="Override orchestrator_home (where _config/projects/ lives).",
    ),
) -> None:
    """Review policy proposals harvested from lessons; accept or reject.

    Scans ``skills/lessons/<project>/`` for ``## Policy proposal`` blocks,
    persists them to ``_config/projects/<project>/policy-proposals.yaml``,
    then prompts (or auto-applies with ``--auto-apply``) each against the
    live policy YAML. Every applied proposal emits a ``policy_proposal_applied``
    audit event with before/after values for rollback.
    """
    _validate_cli_token(project, name="project", pattern=_PROJECT_RE)

    skills_root = safe_resolve_path(skills_root, name="--skills-root")
    if orchestrator_home is not None:
        orchestrator_home = safe_resolve_path(
            orchestrator_home, name="--orchestrator-home"
        )
    if lessons_dir is not None:
        lessons_dir = safe_resolve_path(
            lessons_dir, name="--lessons-dir", must_exist=True
        )
        # Lessons must live under skills_root so a malicious path can't pull
        # a YAML proposal from an attacker-controlled directory.
        lessons_dir = ensure_inside_root(
            lessons_dir,
            skills_root,
            child_name="--lessons-dir",
            root_name="--skills-root",
        )

    from bmad_orchestrator.runtime.lesson_parser import (
        LessonProposal,
        LessonProposalInvalidError,
        apply_proposals_batch,
        parse_lessons_dir,
        save_proposals_yaml,
    )

    settings = load_settings()
    home = orchestrator_home or settings.orchestrator_home
    lessons = lessons_dir or (skills_root / "lessons" / project)

    try:
        proposals = parse_lessons_dir(lessons)
    except LessonProposalInvalidError as exc:
        console.print(f"[red]invalid lesson markdown:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    if not proposals:
        console.print(f"[dim]no proposals found under {lessons}[/dim]")
        return

    out_path = save_proposals_yaml(
        proposals, slug=project, orchestrator_home=home
    )
    console.print(
        f"[cyan]found:[/cyan] {len(proposals)} proposal(s) → {out_path}"
    )

    def _prompt(proposal: LessonProposal) -> bool:
        console.print(
            f"\n[bold]{proposal.policy_file}.{proposal.field}[/bold]"
            f"  [dim]({proposal.source_file})[/dim]"
        )
        console.print(f"  before: {proposal.before!r}")
        console.print(f"  after:  {proposal.after!r}")
        if proposal.rationale:
            console.print(f"  rationale: {proposal.rationale}")
        return typer.confirm("apply?", default=False)

    result = apply_proposals_batch(
        proposals,
        skills_root=skills_root,
        auto_apply=auto_apply,
        prompt=None if auto_apply else _prompt,
    )

    console.print(
        f"\n[green]applied:[/green] {len(result.applied)}  "
        f"[yellow]rejected:[/yellow] {len(result.rejected)}  "
        f"[red]errors:[/red] {len(result.errors)}"
    )
    for prop, msg in result.errors:
        console.print(
            f"  [red]error[/red] {prop.policy_file}.{prop.field}: {msg}"
        )
    if result.errors:
        raise typer.Exit(code=1)


@app.command(name="policy-rollback")
def policy_rollback(
    project: str = typer.Argument(..., help="Project slug (e.g. odyssey)"),
    proposal_id: str = typer.Argument(
        ...,
        help=(
            "Backup timestamp stamped onto the .yaml.bak-<ts> file "
            "(value of `proposal_id` in the policy_proposal_applied audit entry)."
        ),
    ),
    skills_root: Path = typer.Option(  # noqa: B008 — typer pattern
        _DEFAULT_SKILLS_ROOT,
        "--skills-root",
        help="Root of orchestrator skills dir (default: <repo>/skills).",
    ),
) -> None:
    """Restore a previously-applied policy YAML from its on-disk backup.

    Located by ``skills/policy/<name>.yaml.bak-<proposal-id>``. Writes the
    restored payload through :func:`runtime.lesson_parser._atomic_yaml_write`
    so concurrent live-tuning writers see a complete file at all times.
    Emits a ``policy_proposal_rolled_back`` audit entry on success.
    """
    _validate_cli_token(project, name="project", pattern=_PROJECT_RE)
    # ``proposal_id`` becomes the suffix of ``.yaml.bak-<ts>``; same charset
    # constraints as a story id so a malformed timestamp can't path-traverse.
    _validate_cli_token(proposal_id, name="proposal-id", pattern=_STORY_RE)

    skills_root = safe_resolve_path(skills_root, name="--skills-root")

    from bmad_orchestrator.runtime.lesson_parser import (
        PolicyApplyError,
        rollback_policy,
    )

    _ = project  # accepted for symmetry with policy-apply; backups are project-agnostic.
    try:
        restored, backup = rollback_policy(
            skills_root=skills_root, proposal_id=proposal_id
        )
    except PolicyApplyError as exc:
        console.print(f"[red]rollback failed:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    console.print(
        f"[green]restored[/green] {restored.name} from "
        f"[cyan]{backup.name}[/cyan]"
    )


@app.command(name="skill-status")
def skill_status_cmd(
    skills_root: Path = typer.Option(  # noqa: B008 — typer pattern
        _DEFAULT_SKILLS_ROOT,
        "--skills-root",
        help="Root of orchestrator skills dir (default: <repo>/skills).",
    ),
) -> None:
    """Show current upstream version + applied patches + pending conflict reports."""
    skills_root = safe_resolve_path(skills_root, name="--skills-root")

    from bmad_orchestrator.runtime.skill_update import skill_status

    snapshot = skill_status(skills_root)
    console.print(_skill_status_table(snapshot))


# ── bot ──────────────────────────────────────────────────────────────────────


bot_app = typer.Typer(help="Telegram bot management")
app.add_typer(bot_app, name="bot")


@bot_app.command("start")
def bot_start(
    daemon: bool = typer.Option(False, "--daemon", help="Run detached"),
) -> None:
    """Запустить Telegram bot daemon."""
    if daemon:
        proc = subprocess.Popen(
            [sys.executable, "-m", "bmad_orchestrator.bot.main"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        console.print(f"[green]bot daemon pid={proc.pid}[/green]")
        return
    from bmad_orchestrator.bot.telegram_bot import run_bot

    run_bot()


@bot_app.command("stop")
def bot_stop() -> None:
    """Остановить Telegram bot (kill by name)."""
    console.print("[cyan]→[/cyan] bot stop (manual: pkill -f bmad_orchestrator.bot.main)")


@self_learning_app.command("run")
def sl_run(
    trigger: str = typer.Option(
        "manual",
        "--trigger",
        help="Trigger event name (e.g. wave_boundary_reached, monthly_review_scheduled).",
    ),
    config_path: str | None = typer.Option(
        None,
        "--config",
        help="Path to self-learning.yaml. Default: config/self-learning.yaml.",
    ),
) -> None:
    """Run self-learning consolidation once (manual trigger)."""
    from bmad_orchestrator.runtime.self_learning_subscriber import load_self_learning_config
    from bmad_orchestrator.self_learning.consolidator import Consolidator

    cfg_path = Path(config_path) if config_path else None
    cfg = load_self_learning_config(cfg_path)
    if not cfg.enabled:
        console.print("[yellow]self-learning is disabled (check config)[/yellow]")
        raise typer.Exit(1)

    consolidator = Consolidator(config=cfg)
    result = asyncio.run(consolidator.run(trigger_event=trigger))
    console.print(f"[green]consolidation done[/green] trigger={trigger}")
    console.print(f"  proposals: {result.total_proposals} (low={len(result.low_risk)} medium={len(result.medium_risk)} high={len(result.high_risk)})")
    if result.errors:
        for err in result.errors:
            console.print(f"  [red]error:[/red] {err}")


@self_learning_app.command("status")
def sl_status(
    config_path: str | None = typer.Option(
        None,
        "--config",
        help="Path to self-learning.yaml.",
    ),
) -> None:
    """Show self-learning configuration and status."""
    from bmad_orchestrator.runtime.self_learning_subscriber import load_self_learning_config

    cfg_path = Path(config_path) if config_path else None
    cfg = load_self_learning_config(cfg_path)
    table = Table(title="Self-learning config")
    table.add_column("setting")
    table.add_column("value")
    table.add_row("enabled", str(cfg.enabled))
    table.add_row("version", str(cfg.version))
    table.add_row("min_pattern_occurrences", str(cfg.defaults.min_pattern_occurrences))
    table.add_row("auto_apply_max_risk", cfg.defaults.auto_apply_max_risk)
    table.add_row("measure_window_waves", str(cfg.defaults.measure_window_waves))
    table.add_row("regression_threshold_pct", str(cfg.defaults.regression_threshold_pct))
    table.add_row("excluded_policy_files", ", ".join(cfg.excluded_policy_files))
    table.add_row("excluded_compliance_tags", ", ".join(cfg.excluded_compliance_tags))
    console.print(table)


@self_learning_app.command("rollback")
def sl_rollback(
    proposal_id: str = typer.Argument(
        ...,
        help="Backup timestamp (proposal_id) to rollback. See audit log for IDs.",
    ),
    skills_root: str = typer.Option(
        "skills",
        "--skills-root",
        help="Path to skills/ directory containing policy/ subdirectory.",
    ),
) -> None:
    """Rollback a previously applied proposal by its proposal_id."""
    from bmad_orchestrator.runtime.lesson_parser import PolicyApplyError, rollback_policy

    try:
        restored, backup = rollback_policy(
            skills_root=Path(skills_root),
            proposal_id=proposal_id,
        )
        console.print(f"[green]rollback ok[/green] restored={restored.name} from={backup.name}")
    except PolicyApplyError as exc:
        console.print(f"[red]rollback failed:[/red] {exc}")
        raise typer.Exit(1) from exc


@self_learning_app.command("cron-emit")
def sl_cron_emit(
    state_db: str = typer.Option(
        "./state.db",
        "--state-db",
        help="Path to state.db (used for last_emit persistence).",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Emit regardless of date check (for testing).",
    ),
) -> None:
    """Emit MONTHLY_REVIEW_SCHEDULED event (for systemd timer / crontab).

    Checks whether a monthly emit is due; exits quietly if not (idempotent).
    Use --force to emit unconditionally.
    """
    from datetime import UTC, datetime

    from bmad_orchestrator.runtime.event_loop import EventLoop, EventType
    from bmad_orchestrator.runtime.monthly_scheduler import _should_emit

    now = datetime.now(UTC)
    if not force and not _should_emit(now, None):
        console.print(f"[dim]no emit needed (day={now.day}, hour={now.hour})[/dim]")
        return

    async def _emit() -> None:
        bus = EventLoop()
        await bus.emit(
            EventType.MONTHLY_REVIEW_SCHEDULED,
            source="cron_emit",
            triggered_at=now.isoformat(),
        )

    asyncio.run(_emit())
    console.print("[green]MONTHLY_REVIEW_SCHEDULED emitted[/green]")


# ── BMad Phase 4 canonical workflows — manual triggers (gap-closure 2026-05-19) ──


@app.command("sprint-planning")
def sprint_planning_cli(
    real: bool = typer.Option(
        False, "--real/--mock", help="--real spawns claude -p; --mock writes seed yaml from epics.md."
    ),
) -> None:
    """Init sprint-status.yaml via bmad-sprint-planning skill.

    Mock mode (default): parses target's epics.md and writes a seed
    sprint-status.yaml. Real mode forks `claude -p /bmad-sprint-planning`
    inside the target worktree (requires `claude` binary).
    """
    import asyncio as _asyncio

    from bmad_orchestrator.agent.tools.sprint_planning import (
        SprintStatusMissingError,
        ensure_sprint_status_initialized,
        spawn_sprint_planning_worktree,
    )

    if not real:
        try:
            result = ensure_sprint_status_initialized()
        except SprintStatusMissingError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1) from exc
        console.print(
            f"[green]sprint-planning {result['action']}[/green] "
            f"path={result['path']}"
        )
        return

    async def _go() -> None:
        reply = await spawn_sprint_planning_worktree.handler({"real": True})
        console.print(reply.get("content", [{}])[0].get("text", "<no reply>"))

    _asyncio.run(_go())


@app.command("correct-course")
def correct_course_cli(
    story_id: str = typer.Option(..., "--story", help="Affected story ID (e.g. 1.1)."),
    reason: str = typer.Option(..., "--reason", help="Rationale (PM scope drop, AC change, ...)."),
    real: bool = typer.Option(False, "--real/--mock"),
) -> None:
    """Trigger bmad-correct-course for a mid-sprint scope change.

    Emits SPRINT_SCOPE_CHANGE_DETECTED on the bus; the subscriber spawns the
    skill. Use --real to fork `claude -p /bmad-correct-course`.
    """
    import asyncio as _asyncio

    from bmad_orchestrator.runtime.event_loop import EventLoop, EventType

    async def _go() -> None:
        bus = EventLoop()
        from functools import partial

        from bmad_orchestrator.runtime.phase4_subscribers import (
            correct_course_subscriber,
        )

        bus.on(partial(correct_course_subscriber, bus=bus))
        await bus.emit(
            EventType.SPRINT_SCOPE_CHANGE_DETECTED,
            source="cli",
            story_id=story_id,
            reason=reason,
            real=real,
        )
        # Process the single emitted event.
        await bus.dispatch_one(timeout=1.0)

    _asyncio.run(_go())
    console.print(f"[green]correct-course triggered[/green] story={story_id}")


@app.command("investigate")
def investigate_cli(
    subject: str = typer.Option(..., "--subject", help="Story ID / error class / incident."),
    reason: str = typer.Option(..., "--reason"),
    force: bool = typer.Option(
        True, "--force/--heuristic",
        help="Manual trigger bypasses retry-count heuristic (default --force).",
    ),
    real: bool = typer.Option(False, "--real/--mock"),
) -> None:
    """Trigger bmad-investigate forensic deep-dive.

    Emits FORENSIC_INVESTIGATION_NEEDED on the bus; the subscriber spawns the
    skill. Manual CLI defaults to --force (bypass heuristic gate); use
    --heuristic to require the threshold match.
    """
    import asyncio as _asyncio
    from functools import partial

    from bmad_orchestrator.runtime.event_loop import EventLoop, EventType
    from bmad_orchestrator.runtime.phase4_subscribers import investigate_subscriber

    async def _go() -> None:
        bus = EventLoop()
        bus.on(partial(investigate_subscriber, bus=bus))
        await bus.emit(
            EventType.FORENSIC_INVESTIGATION_NEEDED,
            source="cli",
            subject=subject,
            reason=reason,
            force=force,
            real=real,
        )
        await bus.dispatch_one(timeout=1.0)

    _asyncio.run(_go())
    console.print(f"[green]investigate triggered[/green] subject={subject}")


def main() -> None:
    """Console-script entry point — mirrors ``bmad_orchestrator.cli:app``."""
    os.umask(0o077)
    app()


if __name__ == "__main__":
    os.umask(0o077)
    app()
