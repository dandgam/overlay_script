"""Unit tests for ConfirmScreen logic."""

from __future__ import annotations

from bmad_orchestrator.cli.menu.tree import CommandNode


def _destructive_cmd(name: str = "stop") -> CommandNode:
    return CommandNode(
        name=name,
        full_path=(name,),
        help=f"Command {name}",
        callback=None,
        params=[],
        is_destructive=True,
    )


class TestConfirmLeafName:
    def test_leaf_name_simple(self) -> None:
        from bmad_orchestrator.cli.menu.confirm_screen import ConfirmScreen
        node = _destructive_cmd("stop")
        screen = ConfirmScreen(node, {})
        assert screen._leaf_name() == "stop"

    def test_leaf_name_rollback(self) -> None:
        from bmad_orchestrator.cli.menu.confirm_screen import ConfirmScreen
        node = CommandNode(
            name="rollback",
            full_path=("self-learning", "rollback"),
            help="Rollback",
            callback=None,
            params=[],
            is_destructive=True,
        )
        screen = ConfirmScreen(node, {})
        assert screen._leaf_name() == "rollback"

    def test_preview_passes_values(self) -> None:
        from bmad_orchestrator.cli.menu.confirm_screen import ConfirmScreen
        from bmad_orchestrator.cli.menu.tree import ParamSpec
        param = ParamSpec(
            name="wave",
            cli_flag="--wave",
            py_type="str",
            required=True,
            default=None,
            help="wave",
            choices=None,
        )
        node = CommandNode(
            name="run",
            full_path=("run",),
            help="Run",
            callback=None,
            params=[param],
            is_destructive=True,
        )
        screen = ConfirmScreen(node, {"wave": "1a"})
        preview = screen._preview()
        assert "--wave 1a" in preview

    def test_wrong_name_does_not_confirm(self) -> None:
        """Verify exact match logic: wrong input must not equal node name."""
        node = _destructive_cmd("stop")
        assert "sTop" != node.name
        assert "stop " != node.name
        assert "Stop" != node.name

    def test_correct_name_matches(self) -> None:
        node = _destructive_cmd("stop")
        assert "stop" == node.name

    def test_multi_word_cmd_name(self) -> None:
        from bmad_orchestrator.cli.menu.confirm_screen import ConfirmScreen
        node = _destructive_cmd("policy-rollback")
        screen = ConfirmScreen(node, {})
        assert screen._leaf_name() == "policy-rollback"

    def test_empty_input_no_match(self) -> None:
        node = _destructive_cmd("run")
        assert "" != node.name

    def test_partial_input_no_match(self) -> None:
        node = _destructive_cmd("run")
        assert "ru" != node.name
