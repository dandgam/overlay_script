"""Typer CLI entry point. См. spec §14.1."""

from __future__ import annotations

import typer
from rich.console import Console

app = typer.Typer(help="bmad-orchestrator — autonomous BMad Phase 4 agent")
console = Console()


@app.command()
def run(
    project: str = typer.Option(..., "--project", help="Target project name"),
    wave: str = typer.Option(..., "--wave", help="Wave identifier (e.g. 1a)"),
    max_parallel: int = typer.Option(3, "--max-parallel"),
    model: str | None = typer.Option(None, "--model"),
    watch: bool = typer.Option(False, "--watch", help="Show live TUI dashboard"),
    daemon: bool = typer.Option(False, "--daemon", help="Run in background"),
) -> None:
    """Запустить оркестратор на указанной wave."""
    console.print(f"[cyan]TODO[/cyan]: run wave={wave} project={project}")


@app.command()
def status(live: bool = typer.Option(False, "--live")) -> None:
    """Снимок состояния или live TUI."""
    console.print("[cyan]TODO[/cyan]: render status / live dashboard")


@app.command()
def pause() -> None:
    """Приостановить оркестратора (graceful)."""
    console.print("[cyan]TODO[/cyan]: pause")


@app.command()
def resume() -> None:
    """Продолжить после паузы."""
    console.print("[cyan]TODO[/cyan]: resume")


@app.command()
def stop(graceful: bool = typer.Option(True, "--graceful/--hard")) -> None:
    """Остановить оркестратора."""
    console.print(f"[cyan]TODO[/cyan]: stop graceful={graceful}")


@app.command()
def budget(wave: str | None = typer.Option(None, "--wave")) -> None:
    """Показать текущий бюджет."""
    console.print(f"[cyan]TODO[/cyan]: budget wave={wave}")


@app.command()
def logs(worker: str = typer.Option(..., "--worker"), tail: int = 50) -> None:
    """Tail JSONL событий worker'а."""
    console.print(f"[cyan]TODO[/cyan]: tail logs for {worker}")


@app.command()
def dag(wave: str = typer.Option(..., "--wave")) -> None:
    """Показать DAG в ASCII."""
    console.print(f"[cyan]TODO[/cyan]: render DAG for {wave}")


@app.command()
def retro(wave: str = typer.Option(..., "--wave")) -> None:
    """Запустить retrospective вручную."""
    console.print(f"[cyan]TODO[/cyan]: retro for {wave}")


@app.command(name="validate-policy")
def validate_policy() -> None:
    """Проверить orchestrator-policy.yaml."""
    console.print("[cyan]TODO[/cyan]: validate policy")


@app.command()
def memory(wave: str = typer.Option(..., "--wave")) -> None:
    """Показать lessons из wave."""
    console.print(f"[cyan]TODO[/cyan]: memory for {wave}")


bot_app = typer.Typer(help="Telegram bot management")
app.add_typer(bot_app, name="bot")


@bot_app.command("start")
def bot_start() -> None:
    """Запустить Telegram bot daemon."""
    console.print("[cyan]TODO[/cyan]: bot start")


@bot_app.command("stop")
def bot_stop() -> None:
    """Остановить Telegram bot."""
    console.print("[cyan]TODO[/cyan]: bot stop")


if __name__ == "__main__":
    app()
