"""BrowseScreen — hierarchical navigation per spec §3.6."""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import BindingType
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, ListItem, ListView, Static

from bmad_orchestrator.cli.menu.tree import CommandNode, GroupNode

from .help_overlay import HelpOverlay


class BrowseScreen(Screen[None]):
    BINDINGS: ClassVar[list[BindingType]] = [
        ("up", "cursor_up", "Up"),
        ("down", "cursor_down", "Down"),
        ("k", "cursor_up", "Up"),
        ("j", "cursor_down", "Down"),
        ("enter", "select", "Select"),
        ("b", "go_back", "Back"),
        ("left", "go_back", "Back"),
        ("escape", "go_back", "Back"),
        ("slash", "search_noop", "Search"),
        ("question_mark", "show_help", "Help"),
        ("q", "quit_app", "Quit"),
    ]

    def __init__(self, node: GroupNode) -> None:
        super().__init__()
        self._node = node

    def _breadcrumb(self) -> str:
        if not self._node.full_path:
            return "Virgil — main menu"
        return "main → " + " → ".join(self._node.full_path)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        items: list[ListItem] = []
        for child in self._node.children:
            prefix = "▶ " if child.kind == "group" else "  "
            items.append(ListItem(Label(f"{prefix}{child.name}")))
        yield ListView(*items, id="item-list")
        yield Static("", id="help-hint")
        yield Footer()

    def on_mount(self) -> None:
        self.title = self._breadcrumb()
        self._update_hint()

    def _current_child(self) -> GroupNode | CommandNode | None:
        lv = self.query_one("#item-list", ListView)
        idx = lv.index
        if idx is None or idx < 0 or idx >= len(self._node.children):
            return None
        return self._node.children[idx]

    def _update_hint(self) -> None:
        child = self._current_child()
        hint = self.query_one("#help-hint", Static)
        if child is not None:
            hint.update(child.help or "")
        else:
            hint.update("")

    def on_list_view_highlighted(self) -> None:
        self._update_hint()

    def action_cursor_up(self) -> None:
        self.query_one("#item-list", ListView).action_cursor_up()
        self._update_hint()

    def action_cursor_down(self) -> None:
        self.query_one("#item-list", ListView).action_cursor_down()
        self._update_hint()

    def action_select(self) -> None:
        child = self._current_child()
        if child is None:
            return
        if child.kind == "group":
            self.app.push_screen(BrowseScreen(child))
        else:
            self.notify(f"form not yet implemented (M2): {child.name}", title="Coming soon")

    def action_go_back(self) -> None:
        if self.app.screen_stack and len(self.app.screen_stack) > 1:
            self.app.pop_screen()

    def action_search_noop(self) -> None:
        self.notify("Search coming in M2 (/)", title="Not implemented")

    def action_show_help(self) -> None:
        self.app.push_screen(HelpOverlay())

    def action_quit_app(self) -> None:
        self.app.exit()
