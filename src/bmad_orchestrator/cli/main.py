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
import os
import subprocess
import sys
from pathlib import Path

import typer
import yaml
from rich.console import Console
from rich.table import Table

from bmad_orchestrator.agent.run import run_orchestrator
from bmad_orchestrator.cli import models_yaml
from bmad_orchestrator.cli.i18n import t
from bmad_orchestrator.cli.tui import DashboardSnapshot, render_once, run_live
from bmad_orchestrator.config import ModelConfig, load_settings

app = typer.Typer(
    help="bmad-orchestrator — autonomous BMad Phase 4 agent",
    no_args_is_help=True,
)
console = Console()


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
    """Best-effort dashboard snapshot (S8: minimal; runtime-fed in pilot)."""
    settings = load_settings()
    return DashboardSnapshot(
        status="idle",
        wave=wave,
        project=project or settings.target_project.name,
        progress_done=0,
        progress_total=0,
        budget_spent_usd=0.0,
        budget_cap_usd=settings.budget.daily_limit_usd,
        budget_level="ok",
        workers=[],
        ready_next=[],
        blocked=[],
        done_recent=[],
        agent_thinking=t("agent.idle"),
        events_tail=[],
    )


# ── run ──────────────────────────────────────────────────────────────────────


@app.command()
def run(
    project: str = typer.Option(..., "--project", help="Target project name"),
    wave: str = typer.Option(..., "--wave", help="Wave identifier (e.g. 1a)"),
    max_parallel: int = typer.Option(3, "--max-parallel"),
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
            "use --real for real-mode (requires ANTHROPIC_API_KEY + claude "
            "binary)."
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
        f"[cyan]starting[/cyan] project={project} wave={wave} "
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
def budget(wave: str | None = typer.Option(None, "--wave")) -> None:
    """Показать текущий бюджет."""
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


def main() -> None:
    """Console-script entry point — mirrors ``bmad_orchestrator.cli:app``."""
    os.umask(0o077)
    app()


if __name__ == "__main__":
    os.umask(0o077)
    app()
