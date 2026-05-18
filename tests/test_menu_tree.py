"""Unit tests for menu tree pydantic schemas."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from bmad_orchestrator.cli.menu.tree import CommandNode, GroupNode, MenuTree, ParamSpec


def _make_param(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "name": "wave",
        "cli_flag": "--wave",
        "py_type": "str",
        "required": True,
        "default": None,
        "help": "Wave identifier",
        "choices": None,
    }
    base.update(overrides)
    return base


def _make_cmd(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "name": "run",
        "full_path": ("run",),
        "help": "Run orchestrator",
        "callback": None,
        "params": [],
    }
    base.update(overrides)
    return base


class TestParamSpec:
    def test_valid_basic(self) -> None:
        p = ParamSpec(**_make_param())  # type: ignore[arg-type]
        assert p.name == "wave"
        assert p.required is True
        assert p.is_path is False
        assert p.is_secret is False

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            ParamSpec(**_make_param(unknown_field="oops"))  # type: ignore[arg-type]

    def test_choices_stored(self) -> None:
        p = ParamSpec(**_make_param(choices=["a", "b"], py_type='Literal["a","b"]'))  # type: ignore[arg-type]
        assert p.choices == ["a", "b"]

    def test_is_path_flag(self) -> None:
        p = ParamSpec(**_make_param(is_path=True, py_type="Path"))  # type: ignore[arg-type]
        assert p.is_path is True

    def test_optional_default_none(self) -> None:
        p = ParamSpec(**_make_param(required=False, default="1a"))  # type: ignore[arg-type]
        assert p.required is False
        assert p.default == "1a"


class TestCommandNode:
    def test_valid(self) -> None:
        node = CommandNode(**_make_cmd())  # type: ignore[arg-type]
        assert node.kind == "command"
        assert node.name == "run"
        assert node.full_path == ("run",)
        assert node.is_destructive is False

    def test_discriminator(self) -> None:
        node = CommandNode(**_make_cmd())  # type: ignore[arg-type]
        assert node.kind == "command"

    def test_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            CommandNode(**_make_cmd(bad_field=True))  # type: ignore[arg-type]

    def test_with_params(self) -> None:
        param = ParamSpec(**_make_param())  # type: ignore[arg-type]
        node = CommandNode(**_make_cmd(params=[param]))  # type: ignore[arg-type]
        assert len(node.params) == 1
        assert node.params[0].name == "wave"

    def test_destructive_flag(self) -> None:
        node = CommandNode(**_make_cmd(is_destructive=True))  # type: ignore[arg-type]
        assert node.is_destructive is True


class TestGroupNode:
    def test_valid_empty(self) -> None:
        g = GroupNode(name="root", full_path=(), help="root", children=[])
        assert g.kind == "group"
        assert g.children == []

    def test_discriminator(self) -> None:
        g = GroupNode(name="root", full_path=(), help="root", children=[])
        assert g.kind == "group"

    def test_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            GroupNode(name="root", full_path=(), help="root", children=[], bad=True)  # type: ignore[call-arg]

    def test_recursive_children(self) -> None:
        inner_cmd = CommandNode(**_make_cmd(name="status", full_path=("self-learning", "status")))  # type: ignore[arg-type]
        inner_group = GroupNode(
            name="self-learning",
            full_path=("self-learning",),
            help="Self-learning group",
            children=[inner_cmd],
        )
        root = GroupNode(name="root", full_path=(), help="root", children=[inner_group])
        assert len(root.children) == 1
        child = root.children[0]
        assert child.kind == "group"
        assert isinstance(child, GroupNode)
        assert len(child.children) == 1
        assert child.children[0].name == "status"

    def test_menu_tree_alias(self) -> None:
        g = GroupNode(name="root", full_path=(), help="root", children=[])
        assert isinstance(g, MenuTree)

    def test_forward_ref_resolved(self) -> None:
        cmd = CommandNode(**_make_cmd())  # type: ignore[arg-type]
        group = GroupNode(name="sub", full_path=("sub",), help="sub", children=[cmd])
        root = GroupNode(name="root", full_path=(), help="root", children=[group])
        assert root.children[0].kind == "group"
