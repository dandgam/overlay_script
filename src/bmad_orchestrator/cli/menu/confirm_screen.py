"""ConfirmScreen — typed-name confirmation for destructive commands per spec §6."""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.screen import Screen
from textual.widgets import Footer, Header, Input, Label, Static

from bmad_orchestrator.cli.menu.tree import CommandNode


class ConfirmScreen(Screen[bool]):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("enter", "confirm", "Подтвердить", priority=True),
        Binding("escape", "go_back", "Отмена"),
    ]

    DEFAULT_CSS = """
    ConfirmScreen #warning-panel {
        height: 5;
        padding: 1 2;
        border: solid $error;
        color: $error;
    }
    ConfirmScreen #preview-label {
        height: 2;
        padding: 0 1;
        color: $text-muted;
    }
    ConfirmScreen #confirm-label {
        height: 1;
        padding: 0 1;
        color: $warning;
    }
    ConfirmScreen #match-error {
        height: 1;
        padding: 0 1;
        color: $error;
    }
    """

    def __init__(self, node: CommandNode, values: dict[str, str]) -> None:
        super().__init__()
        self._node = node
        self._values = values

    def _leaf_name(self) -> str:
        return self._node.name

    def _preview(self) -> str:
        from bmad_orchestrator.cli.menu.form_screen import _build_cli_preview
        return _build_cli_preview(self._node, self._values)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Static(
            "[bold red]ВНИМАНИЕ: Команда ДЕСТРУКТИВНА.[/bold red]\n"
            "Действие может быть необратимым. Продолжайте только если уверены.",
            id="warning-panel",
            markup=True,
        )
        yield Label(f"Команда: {self._preview()}", id="preview-label")
        yield Label(
            f"Введите [bold]{self._leaf_name()}[/bold] для подтверждения:",
            id="confirm-label",
            markup=True,
        )
        yield Input(placeholder=self._leaf_name(), id="confirm-input")
        yield Static("", id="match-error")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "Подтверждение деструктивной команды"

    def action_confirm(self) -> None:
        widget = self.query_one("#confirm-input", Input)
        typed = widget.value.strip()
        if typed != self._leaf_name():
            self.query_one("#match-error", Static).update(
                f"[red]Ожидалось '{self._leaf_name()}', получено '{typed}'[/red]"
            )
            return
        from bmad_orchestrator.cli.menu.execute_screen import ExecuteScreen
        self.app.push_screen(ExecuteScreen(self._node, self._values))

    def action_go_back(self) -> None:
        if len(self.app.screen_stack) > 1:
            self.app.pop_screen()
