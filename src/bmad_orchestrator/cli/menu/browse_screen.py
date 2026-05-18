"""BrowseScreen — hierarchical navigation per spec §3.6 + search per §6 Session 2."""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.screen import Screen
from textual.widgets import Footer, Header, Input, Label, ListItem, ListView, Static

from bmad_orchestrator.cli.menu.tree import CommandNode, GroupNode

from .help_overlay import HelpOverlay


def filter_items(
    children: list[GroupNode | CommandNode], query: str
) -> list[GroupNode | CommandNode]:
    """Return items whose name contains query (case-insensitive)."""
    if not query:
        return children
    q = query.lower()
    return [c for c in children if q in c.name.lower()]


class BrowseScreen(Screen[None]):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("up", "cursor_up", "Up"),
        Binding("down", "cursor_down", "Down"),
        Binding("k", "cursor_up", "Up"),
        Binding("j", "cursor_down", "Down"),
        Binding("enter", "select", "Select"),
        Binding("b", "go_back", "Back"),
        Binding("left", "go_back", "Back"),
        Binding("escape", "escape_or_back", "Back"),
        Binding("slash", "enter_search", "Search"),
        Binding("question_mark", "show_help", "Help"),
        Binding("q", "quit_app", "Quit"),
    ]

    def __init__(self, node: GroupNode) -> None:
        super().__init__()
        self._node = node
        self._search_mode = False
        self._search_query = ""
        self._filtered: list[GroupNode | CommandNode] = list(node.children)

    def _breadcrumb(self) -> str:
        if not self._node.full_path:
            return "Virgil — main menu"
        return "main → " + " → ".join(self._node.full_path)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Input(placeholder="/ search…", id="search-input", classes="hidden")
        items: list[ListItem] = []
        for child in self._filtered:
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
        if idx is None or idx < 0 or idx >= len(self._filtered):
            return None
        return self._filtered[idx]

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
            from bmad_orchestrator.cli.menu.form_screen import FormScreen
            self.app.push_screen(FormScreen(child))

    def action_go_back(self) -> None:
        if self.app.screen_stack and len(self.app.screen_stack) > 1:
            self.app.pop_screen()

    def action_escape_or_back(self) -> None:
        if self._search_mode:
            self._exit_search()
        else:
            self.action_go_back()

    def action_enter_search(self) -> None:
        self._search_mode = True
        search_input = self.query_one("#search-input", Input)
        search_input.remove_class("hidden")
        search_input.focus()

    def _exit_search(self) -> None:
        self._search_mode = False
        self._search_query = ""
        search_input = self.query_one("#search-input", Input)
        search_input.add_class("hidden")
        search_input.value = ""
        self._apply_filter("")
        self.query_one("#item-list", ListView).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search-input":
            self._search_query = event.value
            self._apply_filter(event.value)

    def _apply_filter(self, query: str) -> None:
        self._filtered = filter_items(self._node.children, query)
        lv = self.query_one("#item-list", ListView)
        lv.clear()
        for child in self._filtered:
            prefix = "▶ " if child.kind == "group" else "  "
            lv.append(ListItem(Label(f"{prefix}{child.name}")))
        self._update_hint()

    def action_show_help(self) -> None:
        self.app.push_screen(HelpOverlay())

    def action_quit_app(self) -> None:
        self.app.exit()
