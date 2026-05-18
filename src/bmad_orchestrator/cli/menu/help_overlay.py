"""HelpOverlay — modal screen showing key bindings."""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import BindingType
from textual.screen import ModalScreen
from textual.widgets import Static

_HELP_TEXT = """\
[bold]Горячие клавиши[/bold]

  ↑ / k       Вверх
  ↓ / j       Вниз
  Enter       Выбрать (войти в группу / запустить команду)
  b / ←       Назад
  /           Поиск
  ?           Открыть/закрыть справку
  q           Выход
"""


class HelpOverlay(ModalScreen[None]):
    BINDINGS: ClassVar[list[BindingType]] = [
        ("escape", "dismiss", "Закрыть"),
        ("question_mark", "dismiss", "Закрыть"),
    ]

    def compose(self) -> ComposeResult:
        yield Static(_HELP_TEXT, id="help-text")

    def action_dismiss(self) -> None:  # type: ignore[override]
        self.dismiss(None)
