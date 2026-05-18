"""Unit tests for ExecuteScreen helpers (_cast_value, _build_kwargs)."""

from __future__ import annotations

from pathlib import Path

import pytest
import typer

from bmad_orchestrator.cli.menu.execute_screen import _build_kwargs, _cast_value
from bmad_orchestrator.cli.menu.tree import CommandNode, ParamSpec


def _param(
    name: str,
    py_type: str,
    required: bool = False,
    default: object | None = None,
    is_path: bool = False,
) -> ParamSpec:
    return ParamSpec(
        name=name,
        cli_flag=f"--{name}",
        py_type=py_type,
        required=required,
        default=default,
        help=f"Help for {name}",
        choices=None,
        is_path=is_path,
    )


class TestCastValue:
    def test_str_passthrough(self) -> None:
        p = _param("wave", "str")
        assert _cast_value("1a", p) == "1a"

    def test_int_cast(self) -> None:
        p = _param("count", "int")
        result = _cast_value("5", p)
        assert result == 5
        assert isinstance(result, int)

    def test_bool_true_variants(self) -> None:
        p = _param("verbose", "bool")
        assert _cast_value("true", p) is True
        assert _cast_value("1", p) is True
        assert _cast_value("yes", p) is True

    def test_bool_false_variants(self) -> None:
        p = _param("verbose", "bool")
        assert _cast_value("false", p) is False
        assert _cast_value("0", p) is False
        assert _cast_value("no", p) is False

    def test_path_cast(self) -> None:
        p = _param("output", "Path", is_path=True)
        result = _cast_value("/tmp/out", p)
        assert isinstance(result, Path)
        assert str(result) == "/tmp/out"

    def test_empty_str_returns_none(self) -> None:
        p = _param("wave", "str")
        assert _cast_value("", p) is None

    def test_empty_int_returns_zero(self) -> None:
        p = _param("count", "int")
        assert _cast_value("", p) == 0

    def test_empty_bool_returns_false(self) -> None:
        p = _param("flag", "bool")
        assert _cast_value("", p) is False


class TestBuildKwargs:
    def test_no_callback_returns_empty(self) -> None:
        node = CommandNode(
            name="status",
            full_path=("status",),
            help="status",
            callback=None,
            params=[],
        )
        result = _build_kwargs(node, {})
        assert result == {}

    def test_maps_params_to_kwargs(self) -> None:
        def my_cmd(wave: str = "1a", count: int = 3) -> None:
            pass

        node = CommandNode(
            name="run",
            full_path=("run",),
            help="run",
            callback=my_cmd,
            params=[
                _param("wave", "str"),
                _param("count", "int"),
            ],
        )
        result = _build_kwargs(node, {"wave": "2b", "count": "7"})
        assert result["wave"] == "2b"
        assert result["count"] == 7

    def test_missing_param_uses_default(self) -> None:
        def my_cmd(wave: str = "1a") -> None:
            pass

        node = CommandNode(
            name="run",
            full_path=("run",),
            help="run",
            callback=my_cmd,
            params=[_param("wave", "str")],
        )
        result = _build_kwargs(node, {})
        assert result.get("wave") == "1a"

    def test_callback_raises_typer_exit(self) -> None:
        def fail_cmd() -> None:
            raise typer.Exit(2)

        node = CommandNode(
            name="fail",
            full_path=("fail",),
            help="fail",
            callback=fail_cmd,
            params=[],
        )
        kwargs = _build_kwargs(node, {})
        with pytest.raises(typer.Exit) as exc_info:
            fail_cmd(**kwargs)
        assert exc_info.value.exit_code == 2

    def test_callback_stdout_hello(self, capsys: pytest.CaptureFixture[str]) -> None:
        def hello_cmd() -> None:
            print("hello")

        node = CommandNode(
            name="hello",
            full_path=("hello",),
            help="hello",
            callback=hello_cmd,
            params=[],
        )
        kwargs = _build_kwargs(node, {})
        hello_cmd(**kwargs)
        captured = capsys.readouterr()
        assert "hello" in captured.out

    def test_callback_raises_exception(self) -> None:
        def boom_cmd() -> None:
            raise RuntimeError("boom")

        node = CommandNode(
            name="boom",
            full_path=("boom",),
            help="boom",
            callback=boom_cmd,
            params=[],
        )
        kwargs = _build_kwargs(node, {})
        with pytest.raises(RuntimeError, match="boom"):
            boom_cmd(**kwargs)
