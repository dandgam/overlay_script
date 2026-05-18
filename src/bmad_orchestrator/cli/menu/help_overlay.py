"""HelpOverlay — modal screen showing key bindings."""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import BindingType
from textual.screen import ModalScreen
from textual.widgets import Static

_HELP_TEXT = """\
[bold]Key bindings[/bold]

  ↑ / k       Navigate up
  ↓ / j       Navigate down
  Enter       Select item (enter group / run command placeholder)
  b / ←       Go back
  /           Search (M2 — not yet implemented)
  ?           Toggle this help
  q           Quit
"""


class HelpOverlay(ModalScreen[None]):
    BINDINGS: ClassVar[list[BindingType]] = [
        ("escape", "dismiss", "Close"),
        ("question_mark", "dismiss", "Close"),
    ]

    def compose(self) -> ComposeResult:
        yield Static(_HELP_TEXT, id="help-text")

    def action_dismiss(self) -> None:  # type: ignore[override]
        self.dismiss(None)
