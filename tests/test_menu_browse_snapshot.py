"""Snapshot test for BrowseScreen using pytest-textual-snapshot."""

from __future__ import annotations

import pytest

pytest.importorskip("pytest_textual_snapshot", reason="pytest-textual-snapshot not installed")

from pytest_textual_snapshot import SnapshotAssertion

from bmad_orchestrator.cli.menu.app import MenuApp
from bmad_orchestrator.cli.menu.tree import CommandNode, GroupNode


def _make_fixture_tree() -> GroupNode:
    cmd_run = CommandNode(
        name="run",
        full_path=("run",),
        help="Run orchestrator on wave",
        callback=None,
        params=[],
        is_destructive=True,
    )
    cmd_status = CommandNode(
        name="status",
        full_path=("status",),
        help="Show current status",
        callback=None,
        params=[],
    )
    sub_cmd = CommandNode(
        name="rollback",
        full_path=("self-learning", "rollback"),
        help="Rollback policy",
        callback=None,
        params=[],
        is_destructive=True,
    )
    sub_group = GroupNode(
        name="self-learning",
        full_path=("self-learning",),
        help="Self-learning commands",
        children=[sub_cmd],
    )
    return GroupNode(
        name="root",
        full_path=(),
        help="Virgil — main menu",
        children=[cmd_run, cmd_status, sub_group],
    )


def test_browse_screen_snapshot(snap_compare: SnapshotAssertion) -> None:
    root = _make_fixture_tree()
    app = MenuApp(root)
    assert snap_compare(app, terminal_size=(80, 24))
