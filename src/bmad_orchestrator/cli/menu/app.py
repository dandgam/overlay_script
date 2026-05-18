"""MenuApp — Textual application entry point per spec §3.6."""

from __future__ import annotations

import typer
from textual.app import App, ComposeResult

from bmad_orchestrator.cli.menu.browse_screen import BrowseScreen
from bmad_orchestrator.cli.menu.discovery import discover
from bmad_orchestrator.cli.menu.tree import GroupNode


class MenuApp(App[None]):
    CSS = """
    #help-hint {
        height: 3;
        padding: 0 1;
        color: $text-muted;
    }
    HelpOverlay {
        align: center middle;
    }
    HelpOverlay #help-text {
        width: 60;
        height: 20;
        border: solid $primary;
        padding: 1 2;
    }
    """

    def __init__(self, root: GroupNode) -> None:
        super().__init__()
        self._root = root

    def compose(self) -> ComposeResult:
        return iter([])

    def on_mount(self) -> None:
        self.push_screen(BrowseScreen(self._root))


def launch_menu(typer_app: typer.Typer) -> None:
    root = discover(typer_app)
    MenuApp(root).run()


__all__ = ["MenuApp", "launch_menu"]
