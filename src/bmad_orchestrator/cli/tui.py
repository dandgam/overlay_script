"""TUI dashboard на rich.Live per spec §14.2.

5 секций (Layout):
  - header  — status / budget / progress
  - workers — таблица активных worker'ов
  - dag     — what's ready / blocked / done
  - agent   — orchestrator thinking state
  - events  — последние JSONL events

Refresh 2s (configurable). Hotkeys пока не интерактивны — это renderer; реальный
TTY-input loop живёт отдельно (cli/main.status --watch).

Snapshot-friendly: ``render_once(snapshot)`` рендерит один кадр в строку (для
unit-тестов и `--once` режима).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


@dataclass(slots=True)
class DashboardSnapshot:
    """Read-only view used by the renderer.

    Все поля простые scalar/list — снимок легко сериализуется и собирается
    из state.db / EventLoop subscriber, без зависимости от runtime объектов.
    """

    status: str = "idle"  # running / paused / halted / idle
    wave: str | None = None
    project: str | None = None
    progress_done: int = 0
    progress_total: int = 0
    budget_spent_usd: float = 0.0
    budget_cap_usd: float = 0.0
    budget_level: str = "ok"  # ok / alarm / halt
    workers: list[dict[str, Any]] = field(default_factory=list)
    ready_next: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    done_recent: list[str] = field(default_factory=list)
    agent_thinking: str = ""
    events_tail: list[str] = field(default_factory=list)
    # Supervisor LLM-loop activity feed (Phase 4 #9 — last N decisions).
    # Each entry: {"ts", "event", "tier", "action", "confidence", "reason"}.
    supervisor_recent: list[dict[str, Any]] = field(default_factory=list)
    supervisor_judge_state: str = "idle"  # idle / waiting / error
    supervisor_rate_capacity: str = ""  # e.g. "3/10 used in 60s"


# ── palette per spec §14.2 ───────────────────────────────────────────────────


_STATE_GLYPH: dict[str, tuple[str, str]] = {
    "active":    ("🟢", "green"),
    "review":    ("🟡", "yellow"),
    "halted":    ("🔴", "red"),
    "done":      ("✓",  "dim green"),
    "alarm":     ("⚠",  "bold yellow"),
    "halt":      ("⛔", "bold red"),
    "sleeping":  ("⏸", "cyan"),
    "thinking":  ("💭", "magenta"),
    "ok":        ("·",  "dim"),
    "running":   ("🟢", "green"),
    "paused":    ("⏸", "cyan"),
    "idle":      ("·",  "dim"),
}


def _state_chip(state: str) -> Text:
    glyph, color = _STATE_GLYPH.get(state, ("·", "dim"))
    return Text(f"{glyph} {state}", style=color)


# ── panels ───────────────────────────────────────────────────────────────────


def _header_panel(snap: DashboardSnapshot) -> Panel:
    pieces: list[Text] = [
        Text("Virgil", style="bold cyan"),
        _state_chip(snap.status),
    ]
    if snap.project and snap.wave:
        pieces.append(Text(f"{snap.project}/{snap.wave}", style="bold white"))
    if snap.progress_total > 0:
        pieces.append(
            Text(f"progress: {snap.progress_done}/{snap.progress_total}", style="white")
        )
    if snap.budget_cap_usd > 0:
        chip = _state_chip(snap.budget_level)
        pieces.append(
            Text(f"budget: ${snap.budget_spent_usd:.2f}/${snap.budget_cap_usd:.2f}  ")
            + chip
        )

    body = Text("  ·  ", style="dim").join(pieces)
    return Panel(body, title="status", border_style="cyan")


def _workers_panel(snap: DashboardSnapshot) -> Panel:
    table = Table(expand=True, show_lines=False, header_style="bold cyan")
    table.add_column("worker", style="bold")
    table.add_column("story")
    table.add_column("stage")
    table.add_column("state")
    table.add_column("tokens", justify="right")
    table.add_column("$", justify="right")

    if not snap.workers:
        table.add_row("—", "—", "—", "—", "0", "0.00")
    else:
        for w in snap.workers:
            table.add_row(
                str(w.get("worktree", "?")),
                str(w.get("story_id", "?")),
                str(w.get("stage", "?")),
                str(_state_chip(str(w.get("state", "active")))),
                str(w.get("tokens_used", 0)),
                f"{float(w.get('cost_usd', 0.0)):.2f}",
            )
    return Panel(table, title="workers", border_style="white")


def _dag_panel(snap: DashboardSnapshot) -> Panel:
    table = Table.grid(expand=True, padding=(0, 1))
    table.add_column("title", style="bold white")
    table.add_column("items")

    def _row(label: str, items: list[str], style: str) -> None:
        text = Text(", ".join(items) if items else "—", style=style)
        table.add_row(label, text)

    _row("ready next", snap.ready_next, "green")
    _row("blocked", snap.blocked, "yellow")
    _row("done recent", snap.done_recent, "dim green")
    return Panel(table, title="DAG", border_style="green")


def _agent_panel(snap: DashboardSnapshot) -> Panel:
    body = Text(snap.agent_thinking or "(ожидает события)", style="magenta")
    return Panel(body, title="агент", border_style="magenta")


def _events_panel(snap: DashboardSnapshot) -> Panel:
    lines = snap.events_tail[-12:] if snap.events_tail else ["(нет событий)"]
    body = Text("\n".join(lines), style="dim")
    return Panel(body, title="events", border_style="blue")


def _supervisor_panel(snap: DashboardSnapshot) -> Panel:
    """Supervisor LLM-loop activity feed.

    Shows last decisions (tier/action/confidence) + current judge state + rate
    limit capacity. Empty list when supervisor hasn't decided anything yet.
    """
    if not snap.supervisor_recent:
        body_text = Text(
            f"(no decisions yet)  judge={snap.supervisor_judge_state}",
            style="dim",
        )
        return Panel(body_text, title="supervisor", border_style="bright_blue")

    table = Table.grid(padding=(0, 1))
    table.add_column(style="dim")
    table.add_column(style="cyan")
    table.add_column(style="white")
    table.add_column()
    for entry in snap.supervisor_recent[-6:]:
        tier = str(entry.get("tier", "?"))
        action = str(entry.get("action", "?"))
        conf = entry.get("confidence")
        conf_text = f"{conf:.2f}" if isinstance(conf, int | float) else "-"
        reason = str(entry.get("reason", ""))[:60]
        # Color action by severity
        action_text = Text(action)
        if action == "abort_pipeline":
            action_text.stylize("bold red")
        elif action == "escalate_human":
            action_text.stylize("yellow")
        elif action == "auto_respond":
            action_text.stylize("green")
        elif action == "pause_workers":
            action_text.stylize("bold yellow")
        else:
            action_text.stylize("dim")
        table.add_row(f"T{tier}", action_text, conf_text, reason)

    footer = Text(
        f"  judge={snap.supervisor_judge_state}  "
        f"rate={snap.supervisor_rate_capacity or '0/?'}",
        style="dim",
    )
    return Panel(
        Group(table, footer),
        title="supervisor",
        border_style="bright_blue",
    )


def _hotkeys_panel() -> Panel:
    """Footer per spec §14.2 — `[q]uit [p]ause [r]etro [l]ogs [d]ag [b]udget [/?]`."""
    body = Text(
        "[q]uit  [p]ause  [r]etro  [l]ogs  [d]ag  [b]udget  [/?]",
        style="bold cyan",
    )
    return Panel(body, border_style="cyan")


def build_layout(snap: DashboardSnapshot) -> Layout:
    """Assemble the 5-panel layout per spec §14.2 (+ hotkeys footer)."""
    root = Layout()
    root.split_column(
        Layout(_header_panel(snap), name="header", size=3),
        Layout(name="body"),
        Layout(_hotkeys_panel(), name="footer", size=3),
    )
    root["body"].split_row(
        Layout(name="left"),
        Layout(name="right"),
    )
    root["body"]["left"].split_column(
        Layout(_workers_panel(snap), name="workers"),
        Layout(_dag_panel(snap), name="dag"),
    )
    root["body"]["right"].split_column(
        Layout(_agent_panel(snap), name="agent"),
        Layout(_supervisor_panel(snap), name="supervisor"),
        Layout(_events_panel(snap), name="events"),
    )
    return root


# ── render helpers ───────────────────────────────────────────────────────────


def render_once(snap: DashboardSnapshot, *, width: int = 120) -> str:
    """Render a single frame to plain text (used by --once и unit-тестами)."""
    console = Console(width=width, record=True, color_system=None)
    console.print(build_layout(snap))
    return console.export_text()


SnapshotProvider = Any  # callable returning DashboardSnapshot


def run_live(
    provider: SnapshotProvider,
    *,
    refresh_per_second: float = 0.5,  # 2s default per spec §14.2
    iterations: int | None = None,
) -> None:
    """Run rich.Live loop. ``iterations`` ограничивает количество кадров (для тестов).

    ``provider`` — zero-arg callable, возвращающий DashboardSnapshot. Вызывается
    каждый refresh tick. `iterations=None` — бесконечно (до Ctrl-C).
    """
    snap = provider()
    period = 1.0 / refresh_per_second if refresh_per_second > 0 else 2.0
    n = 0
    with Live(build_layout(snap), refresh_per_second=refresh_per_second, screen=False) as live:
        while iterations is None or n < iterations:
            time.sleep(period)
            snap = provider()
            live.update(build_layout(snap))
            n += 1


__all__ = [
    "DashboardSnapshot",
    "build_layout",
    "render_once",
    "run_live",
]
