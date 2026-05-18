"""ExecuteScreen — runs command in-process + live stdout capture per spec §6."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import io
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

import typer
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.screen import Screen
from textual.widgets import Footer, Header, RichLog, Static

from bmad_orchestrator.cli.menu.tree import CommandNode, ParamSpec


def _cast_value(raw: str, param: ParamSpec) -> Any:
    """Cast string form value to the Python type declared in ParamSpec."""
    if not raw:
        if param.py_type == "bool":
            return False
        if param.py_type == "int":
            return 0
        return None
    if param.py_type == "bool":
        return raw.lower() in ("true", "1", "yes")
    if param.py_type == "int":
        return int(raw)
    if param.is_path:
        return Path(raw)
    return raw


def _build_kwargs(node: CommandNode, values: dict[str, str]) -> dict[str, Any]:
    """Map form values to the callback's parameter names."""
    if node.callback is None:
        return {}
    try:
        sig = inspect.signature(node.callback)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return {}
    kwargs: dict[str, Any] = {}
    for param in node.params:
        if param.name not in sig.parameters:
            continue
        raw = values.get(param.name, "")
        casted = _cast_value(raw, param)
        if casted is not None:
            kwargs[param.name] = casted
        elif not param.required:
            cb_param = sig.parameters[param.name]
            if cb_param.default is not inspect.Parameter.empty:
                kwargs[param.name] = cb_param.default
    return kwargs


class ExecuteScreen(Screen[None]):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+c", "cancel_task", "Отмена", show=True),
    ]

    DEFAULT_CSS = """
    ExecuteScreen #status-bar {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    ExecuteScreen #done-hint {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    """

    def __init__(self, node: CommandNode, values: dict[str, str]) -> None:
        super().__init__()
        self._node = node
        self._values = values
        self._task: asyncio.Task[None] | None = None
        self._done = False
        self._captured = io.StringIO()
        self._flushed_pos = 0

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Static("Выполняется…", id="status-bar")
        yield RichLog(id="output-log", wrap=True, markup=True)
        yield Static("", id="done-hint")
        yield Footer()

    def on_mount(self) -> None:
        cmd = " ".join(["bmad-orchestrator", *list(self._node.full_path)])
        self.title = f"Выполнение: {cmd}"
        self._task = asyncio.create_task(self._run_command())
        self.set_interval(0.1, self._flush_output)

    async def _run_command(self) -> None:
        kwargs = _build_kwargs(self._node, self._values)
        captured = self._captured
        exit_code = 0
        callback = self._node.callback
        if callback is None:
            captured.write("(no callback)\n")
            self._finish(0)
            return
        typed_cb: Callable[..., Any] = callback  # type: ignore[assignment]
        try:
            with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
                await asyncio.to_thread(typed_cb, **kwargs)
        except typer.Exit as exc:
            exit_code = exc.exit_code if exc.exit_code is not None else 0
        except SystemExit as exc:
            exit_code = exc.code if isinstance(exc.code, int) else 0
        except asyncio.CancelledError:
            captured.write("\n[Cancelled]\n")
            self._finish(130)
            return
        except Exception:
            captured.write(traceback.format_exc())
            exit_code = 1
        self._finish(exit_code)

    def _finish(self, exit_code: int) -> None:
        self._done = True
        self._flush_output()
        status = self.query_one("#status-bar", Static)
        hint = self.query_one("#done-hint", Static)
        if exit_code == 0:
            status.update("[green]✅ exit 0 (готово)[/green]")
        else:
            status.update(f"[red]❌ exit {exit_code} (ошибка)[/red]")
        hint.update("[dim]Любая клавиша для возврата[/dim]")

    def _flush_output(self) -> None:
        log = self.query_one("#output-log", RichLog)
        current = self._captured.getvalue()
        new_text = current[self._flushed_pos:]
        if new_text:
            log.write(new_text)
            self._flushed_pos = len(current)

    def on_key(self, event: object) -> None:
        if self._done and len(self.app.screen_stack) > 1:
            self.app.pop_screen()

    def action_cancel_task(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
