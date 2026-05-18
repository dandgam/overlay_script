"""FormScreen — per-parameter form for a CommandNode per spec §6 Session 2."""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import ScrollableContainer
from textual.screen import Screen
from textual.widgets import (
    Checkbox,
    Footer,
    Header,
    Input,
    Label,
    Select,
    Static,
)

from bmad_orchestrator.cli.menu.tree import CommandNode, ParamSpec

from .help_overlay import HelpOverlay


def _build_cli_preview(node: CommandNode, values: dict[str, str]) -> str:
    """Render the resolved CLI command from current form values."""
    parts = ["bmad-orchestrator", *list(node.full_path)]
    for param in node.params:
        raw = values.get(param.name, "")
        if not raw:
            continue
        if param.py_type == "bool":
            if raw.lower() in ("true", "1", "yes"):
                parts.append(param.cli_flag)
        else:
            parts.append(param.cli_flag)
            parts.append(raw)
    return " ".join(parts)


def _param_widget_id(param: ParamSpec) -> str:
    return f"param-{param.name}"


class FormScreen(Screen[dict[str, str] | None]):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("enter", "submit", "Run", priority=True),
        Binding("b", "go_back", "Back"),
        Binding("escape", "go_back", "Back"),
        Binding("question_mark", "show_help", "Help"),
    ]

    DEFAULT_CSS = """
    FormScreen #form-header {
        padding: 0 1;
        color: $text;
        height: 1;
    }
    FormScreen .field-label {
        padding: 0 1;
        color: $text-muted;
        height: 1;
    }
    FormScreen .field-required {
        color: $error;
    }
    FormScreen #preview-box {
        height: 3;
        padding: 0 1;
        color: $success;
        border: solid $primary;
    }
    FormScreen #error-box {
        height: 2;
        padding: 0 1;
        color: $error;
    }
    """

    def __init__(self, node: CommandNode) -> None:
        super().__init__()
        self._node = node
        self._values: dict[str, str] = {}
        self._error: str = ""
        self._init_defaults()

    def _init_defaults(self) -> None:
        for param in self._node.params:
            if param.default is not None:
                self._values[param.name] = str(param.default)
            else:
                self._values[param.name] = ""

    def _full_command(self) -> str:
        return " ".join(self._node.full_path) if self._node.full_path else self._node.name

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Static(f"Command: bmad-orchestrator {self._full_command()}", id="form-header")
        with ScrollableContainer(id="form-scroll"):
            for param in self._node.params:
                required = param.required
                marker = " [red]*[/red]" if required else ""
                label_text = f"{param.cli_flag}{marker}  [dim]{param.help or ''}[/dim]"
                yield Label(label_text, classes="field-label", markup=True)
                widget_id = _param_widget_id(param)
                default_str = str(param.default) if param.default is not None else ""
                if param.choices is not None:
                    options = [(c, c) for c in param.choices]
                    initial = default_str if default_str in param.choices else Select.BLANK
                    yield Select(options, value=initial, id=widget_id, allow_blank=True)
                elif param.py_type == "bool":
                    checked = str(param.default).lower() in ("true", "1", "yes") if param.default is not None else False
                    yield Checkbox(param.cli_flag, value=checked, id=widget_id)
                elif param.py_type == "int":
                    yield Input(
                        value=default_str,
                        placeholder="integer (e.g. 3)",
                        type="integer",
                        id=widget_id,
                    )
                elif param.is_path:
                    yield Input(
                        value=default_str,
                        placeholder="path/to/file",
                        id=widget_id,
                    )
                else:
                    yield Input(
                        value=default_str,
                        placeholder=param.name,
                        id=widget_id,
                        password=param.is_secret,
                    )
        yield Static("", id="error-box")
        yield Static(self._preview_text(), id="preview-box")
        yield Footer()

    def _preview_text(self) -> str:
        return _build_cli_preview(self._node, self._values)

    def on_mount(self) -> None:
        self.title = f"bmad-orchestrator {self._full_command()}"

    def _collect_values(self) -> dict[str, str]:
        values: dict[str, str] = {}
        for param in self._node.params:
            widget_id = _param_widget_id(param)
            if param.choices is not None:
                sel: Select[str] = self.query_one(f"#{widget_id}", Select)
                sel_val = sel.value
                values[param.name] = "" if sel_val is Select.BLANK else str(sel_val)
            elif param.py_type == "bool":
                chk: Checkbox = self.query_one(f"#{widget_id}", Checkbox)
                values[param.name] = "true" if chk.value else "false"
            else:
                inp: Input = self.query_one(f"#{widget_id}", Input)
                values[param.name] = inp.value or ""
        return values

    def _update_preview(self) -> None:
        self._values = self._collect_values()
        self.query_one("#preview-box", Static).update(self._preview_text())

    def on_input_changed(self) -> None:
        self._update_preview()

    def on_select_changed(self) -> None:
        self._update_preview()

    def on_checkbox_changed(self) -> None:
        self._update_preview()

    def _validate_form(self) -> str | None:
        for param in self._node.params:
            val = self._values.get(param.name, "")
            if param.required and not val:
                return f"Required: {param.cli_flag}"
            if val and param.py_type == "int":
                try:
                    int(val)
                except ValueError:
                    return f"Must be integer: {param.cli_flag}"
        return None

    def action_submit(self) -> None:
        self._values = self._collect_values()
        err = self._validate_form()
        if err:
            self.query_one("#error-box", Static).update(f"[red]{err}[/red]")
            return
        self.query_one("#error-box", Static).update("")
        if self._node.is_destructive:
            from bmad_orchestrator.cli.menu.confirm_screen import ConfirmScreen
            self.app.push_screen(ConfirmScreen(self._node, self._values))
        else:
            from bmad_orchestrator.cli.menu.execute_screen import ExecuteScreen
            self.app.push_screen(ExecuteScreen(self._node, self._values))

    def action_go_back(self) -> None:
        if len(self.app.screen_stack) > 1:
            self.app.pop_screen()

    def action_show_help(self) -> None:
        self.app.push_screen(HelpOverlay())
