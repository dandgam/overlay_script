"""Unit tests for FormScreen and form helpers."""

from __future__ import annotations

from bmad_orchestrator.cli.menu.form_screen import _build_cli_preview
from bmad_orchestrator.cli.menu.tree import CommandNode, ParamSpec


def _param(
    name: str,
    py_type: str,
    cli_flag: str | None = None,
    required: bool = False,
    default: object | None = None,
    choices: list[str] | None = None,
    is_path: bool = False,
) -> ParamSpec:
    return ParamSpec(
        name=name,
        cli_flag=cli_flag or f"--{name}",
        py_type=py_type,
        required=required,
        default=default,
        help=f"Help for {name}",
        choices=choices,
        is_path=is_path,
    )


def _cmd(name: str = "run", params: list[ParamSpec] | None = None, is_destructive: bool = False) -> CommandNode:
    return CommandNode(
        name=name,
        full_path=(name,),
        help=f"Command {name}",
        callback=None,
        params=params or [],
        is_destructive=is_destructive,
    )


class TestBuildCliPreview:
    def test_no_params_basic(self) -> None:
        node = _cmd("status")
        result = _build_cli_preview(node, {})
        assert result == "bmad-orchestrator status"

    def test_str_param_filled(self) -> None:
        node = _cmd(params=[_param("wave", "str", "--wave")])
        result = _build_cli_preview(node, {"wave": "1a"})
        assert "--wave 1a" in result

    def test_int_param_filled(self) -> None:
        node = _cmd(params=[_param("count", "int", "--count")])
        result = _build_cli_preview(node, {"count": "5"})
        assert "--count 5" in result

    def test_bool_param_true(self) -> None:
        node = _cmd(params=[_param("verbose", "bool", "--verbose")])
        result = _build_cli_preview(node, {"verbose": "true"})
        assert "--verbose" in result
        assert "true" not in result

    def test_bool_param_false(self) -> None:
        node = _cmd(params=[_param("verbose", "bool", "--verbose")])
        result = _build_cli_preview(node, {"verbose": "false"})
        assert "--verbose" not in result

    def test_empty_param_skipped(self) -> None:
        node = _cmd(params=[_param("wave", "str", "--wave")])
        result = _build_cli_preview(node, {"wave": ""})
        assert "--wave" not in result

    def test_path_param(self) -> None:
        node = _cmd(params=[_param("output", "Path", "--output", is_path=True)])
        result = _build_cli_preview(node, {"output": "/tmp/out"})
        assert "--output /tmp/out" in result

    def test_literal_choice(self) -> None:
        node = _cmd(params=[_param("mode", 'Literal["a","b"]', "--mode", choices=["a", "b"])])
        result = _build_cli_preview(node, {"mode": "b"})
        assert "--mode b" in result

    def test_multi_params_order(self) -> None:
        node = _cmd(params=[
            _param("project", "str", "--project"),
            _param("wave", "str", "--wave"),
        ])
        result = _build_cli_preview(node, {"project": "ant", "wave": "1a"})
        assert result.index("--project") < result.index("--wave")

    def test_full_path_subcommand(self) -> None:
        node = CommandNode(
            name="run",
            full_path=("self-learning", "run"),
            help="SL run",
            callback=None,
            params=[_param("trigger", "str", "--trigger")],
        )
        result = _build_cli_preview(node, {"trigger": "manual"})
        assert result.startswith("bmad-orchestrator self-learning run")
        assert "--trigger manual" in result


class TestParamSpecTypes:
    def test_str_param_spec(self) -> None:
        p = _param("name", "str")
        assert p.py_type == "str"
        assert not p.is_path
        assert not p.is_secret

    def test_int_param_spec(self) -> None:
        p = _param("count", "int", default=3)
        assert p.py_type == "int"
        assert p.default == 3

    def test_bool_param_spec(self) -> None:
        p = _param("verbose", "bool", default=False)
        assert p.py_type == "bool"

    def test_path_param_spec(self) -> None:
        p = _param("output", "Path", is_path=True)
        assert p.is_path is True

    def test_literal_param_spec(self) -> None:
        p = _param("mode", 'Literal["a","b"]', choices=["a", "b"])
        assert p.choices == ["a", "b"]

    def test_required_marker(self) -> None:
        p = _param("wave", "str", required=True)
        assert p.required is True
        assert p.default is None

    def test_default_prefill(self) -> None:
        p = _param("max_parallel", "int", default=3)
        assert str(p.default) == "3"
