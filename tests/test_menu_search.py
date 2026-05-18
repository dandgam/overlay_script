"""Unit tests for search filter in BrowseScreen."""

from __future__ import annotations

from bmad_orchestrator.cli.menu.browse_screen import filter_items
from bmad_orchestrator.cli.menu.tree import CommandNode, GroupNode


def _cmd(name: str) -> CommandNode:
    return CommandNode(
        name=name,
        full_path=(name,),
        help=f"Help for {name}",
        callback=None,
        params=[],
    )


def _group(name: str) -> GroupNode:
    return GroupNode(
        name=name,
        full_path=(name,),
        help=f"Group {name}",
        children=[],
    )


class TestFilterItems:
    def setup_method(self) -> None:
        self.items: list[GroupNode | CommandNode] = [
            _cmd("run"),
            _cmd("status"),
            _cmd("stop"),
            _group("self-learning"),
            _group("model"),
            _cmd("dag"),
        ]

    def test_empty_query_returns_all(self) -> None:
        result = filter_items(self.items, "")
        assert len(result) == len(self.items)

    def test_exact_match(self) -> None:
        result = filter_items(self.items, "run")
        assert len(result) == 1
        assert result[0].name == "run"

    def test_substring_match(self) -> None:
        result = filter_items(self.items, "st")
        names = {r.name for r in result}
        assert "status" in names
        assert "stop" in names

    def test_case_insensitive(self) -> None:
        result = filter_items(self.items, "RUN")
        assert len(result) == 1
        assert result[0].name == "run"

    def test_no_match_returns_empty(self) -> None:
        result = filter_items(self.items, "zzz")
        assert result == []

    def test_partial_match_self_learning(self) -> None:
        result = filter_items(self.items, "self")
        assert len(result) == 1
        assert result[0].name == "self-learning"

    def test_hyphen_in_query(self) -> None:
        result = filter_items(self.items, "self-")
        assert len(result) == 1
        assert result[0].name == "self-learning"

    def test_groups_included(self) -> None:
        result = filter_items(self.items, "model")
        assert len(result) == 1
        assert result[0].kind == "group"

    def test_single_char_query(self) -> None:
        result = filter_items(self.items, "s")
        names = {r.name for r in result}
        assert "status" in names
        assert "stop" in names
        assert "self-learning" in names

    def test_preserves_order(self) -> None:
        result = filter_items(self.items, "")
        for i, item in enumerate(result):
            assert item.name == self.items[i].name
