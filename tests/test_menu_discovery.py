"""Unit tests for typer app → MenuTree discovery."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import typer

from bmad_orchestrator.cli.menu.discovery import discover
from bmad_orchestrator.cli.menu.tree import CommandNode, GroupNode


def _make_fixture_app() -> typer.Typer:
    """Small fixture app: 2 top-level commands + 1 subgroup with 1 command."""
    fixture_app = typer.Typer(help="Fixture app for tests")

    @fixture_app.command()
    def hello(
        name: str = typer.Argument(..., help="Name to greet"),
        count: int = typer.Option(1, "--count", help="Repeat count"),
    ) -> None:
        """Say hello."""

    sub = typer.Typer(help="Sub group")
    fixture_app.add_typer(sub, name="sub")

    @sub.command("run")
    def sub_run(
        verbose: bool = typer.Option(False, "--verbose"),
        mode: Literal["a", "b"] = typer.Option("a", "--mode"),
        output: Path | None = typer.Option(None, "--output"),
        required_str: str = typer.Option(..., "--required-str"),
    ) -> None:
        """Sub run command."""

    @fixture_app.command()
    def bye() -> None:
        """Say bye."""

    return fixture_app


class TestFixtureApp:
    def setup_method(self) -> None:
        self.app = _make_fixture_app()
        self.tree = discover(self.app)

    def test_root_is_group(self) -> None:
        assert self.tree.kind == "group"
        assert self.tree.name == "root"

    def test_correct_shape(self) -> None:
        names = {c.name for c in self.tree.children}
        assert "hello" in names
        assert "bye" in names
        assert "sub" in names

    def test_subgroup_found(self) -> None:
        sub = next(c for c in self.tree.children if c.name == "sub")
        assert sub.kind == "group"
        assert isinstance(sub, GroupNode)
        assert len(sub.children) == 1
        assert sub.children[0].name == "run"

    def test_command_help(self) -> None:
        hello = next(c for c in self.tree.children if c.name == "hello")
        assert hello.kind == "command"
        assert "hello" in hello.help.lower()

    def test_int_default_param(self) -> None:
        hello = next(c for c in self.tree.children if c.name == "hello")
        assert isinstance(hello, CommandNode)
        count = next(p for p in hello.params if p.name == "count")
        assert count.required is False
        assert count.default == 1
        assert count.py_type == "int"

    def test_required_argument(self) -> None:
        hello = next(c for c in self.tree.children if c.name == "hello")
        assert isinstance(hello, CommandNode)
        name_p = next(p for p in hello.params if p.name == "name")
        assert name_p.required is True
        assert name_p.default is None

    def test_bool_flag(self) -> None:
        sub = next(c for c in self.tree.children if c.name == "sub")
        assert isinstance(sub, GroupNode)
        run = next(c for c in sub.children if c.name == "run")
        assert isinstance(run, CommandNode)
        verbose = next(p for p in run.params if p.name == "verbose")
        assert verbose.py_type == "bool"
        assert verbose.required is False

    def test_path_param(self) -> None:
        sub = next(c for c in self.tree.children if c.name == "sub")
        assert isinstance(sub, GroupNode)
        run = next(c for c in sub.children if c.name == "run")
        assert isinstance(run, CommandNode)
        output = next(p for p in run.params if p.name == "output")
        assert output.is_path is True

    def test_literal_choices(self) -> None:
        sub = next(c for c in self.tree.children if c.name == "sub")
        assert isinstance(sub, GroupNode)
        run = next(c for c in sub.children if c.name == "run")
        assert isinstance(run, CommandNode)
        mode = next(p for p in run.params if p.name == "mode")
        assert mode.choices is not None
        assert sorted(mode.choices) == ["a", "b"]

    def test_required_option(self) -> None:
        sub = next(c for c in self.tree.children if c.name == "sub")
        assert isinstance(sub, GroupNode)
        run = next(c for c in sub.children if c.name == "run")
        assert isinstance(run, CommandNode)
        req = next(p for p in run.params if p.name == "required_str")
        assert req.required is True


class TestRealApp:
    def setup_method(self) -> None:
        from bmad_orchestrator.cli.main import app

        self.tree = discover(app)

    def _all_commands(self, node: GroupNode) -> list[CommandNode]:
        cmds: list[CommandNode] = []
        for c in node.children:
            if c.kind == "command":
                cmds.append(c)
            else:
                assert isinstance(c, GroupNode)
                cmds.extend(self._all_commands(c))
        return cmds

    def test_at_least_25_commands(self) -> None:
        all_cmds = self._all_commands(self.tree)
        assert len(all_cmds) >= 25

    def test_four_subgroups(self) -> None:
        groups = [c for c in self.tree.children if c.kind == "group"]
        group_names = {g.name for g in groups}
        assert "eval" in group_names
        assert "self-learning" in group_names
        assert "model" in group_names
        assert "bot" in group_names

    def test_stop_is_destructive(self) -> None:
        stop = next(c for c in self.tree.children if c.name == "stop")
        assert isinstance(stop, CommandNode)
        assert stop.is_destructive is True

    def test_run_is_destructive(self) -> None:
        run = next(c for c in self.tree.children if c.name == "run")
        assert isinstance(run, CommandNode)
        assert run.is_destructive is True

    def test_multi_is_destructive(self) -> None:
        multi = next(c for c in self.tree.children if c.name == "multi")
        assert isinstance(multi, CommandNode)
        assert multi.is_destructive is True

    def test_policy_rollback_is_destructive(self) -> None:
        pr = next(c for c in self.tree.children if c.name == "policy-rollback")
        assert isinstance(pr, CommandNode)
        assert pr.is_destructive is True

    def test_skill_update_is_destructive(self) -> None:
        su = next(c for c in self.tree.children if c.name == "skill-update")
        assert isinstance(su, CommandNode)
        assert su.is_destructive is True

    def test_sl_rollback_is_destructive(self) -> None:
        sl = next(c for c in self.tree.children if c.name == "self-learning")
        assert isinstance(sl, GroupNode)
        rb = next(c for c in sl.children if c.name == "rollback")
        assert isinstance(rb, CommandNode)
        assert rb.is_destructive is True

    def test_status_not_destructive(self) -> None:
        st = next(c for c in self.tree.children if c.name == "status")
        assert isinstance(st, CommandNode)
        assert st.is_destructive is False

    def test_dag_not_destructive(self) -> None:
        dag = next(c for c in self.tree.children if c.name == "dag")
        assert isinstance(dag, CommandNode)
        assert dag.is_destructive is False
