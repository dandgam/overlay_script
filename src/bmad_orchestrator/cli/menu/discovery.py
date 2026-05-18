"""Walk typer.Typer app → MenuTree per spec §3.4."""

from __future__ import annotations

import inspect
import pathlib
import typing

import typer
import typer.models

from bmad_orchestrator.cli.menu.safety import is_destructive
from bmad_orchestrator.cli.menu.tree import CommandNode, GroupNode, ParamSpec


def _stringify_annotation(annotation: object) -> str:
    if annotation is inspect.Parameter.empty:
        return "Any"
    if isinstance(annotation, type):
        return annotation.__name__
    return str(annotation)


def _extract_choices(annotation: object) -> list[str] | None:
    origin = typing.get_origin(annotation)
    if origin is typing.Literal:
        return [str(a) for a in typing.get_args(annotation)]
    return None


def _is_path_type(annotation: object) -> bool:
    if annotation is pathlib.Path:
        return True
    if isinstance(annotation, type) and issubclass(annotation, pathlib.Path):
        return True
    # Handle Optional[Path], Path | None, Union[Path, None]
    args = typing.get_args(annotation)
    if args:
        return any(_is_path_type(a) for a in args)
    return False


def _build_param(name: str, param: inspect.Parameter) -> ParamSpec | None:
    default_val = param.default
    annotation = param.annotation

    # Skip parameters that are not typer Option/Argument and have no annotation
    # (likely internal injected args like ctx, etc.)
    if isinstance(default_val, typer.models.OptionInfo):
        info: typer.models.OptionInfo = default_val
        actual_default = info.default
        required = actual_default is ...
        help_text = info.help
        # param_decls is the list of CLI flags e.g. ["--wave", "-w"]
        param_decls: list[str] = list(info.param_decls) if info.param_decls else []
        cli_flag = param_decls[0] if param_decls else f"--{name.replace('_', '-')}"
        if required:
            actual_default = None
    elif isinstance(default_val, typer.models.ArgumentInfo):
        arg_info: typer.models.ArgumentInfo = default_val
        actual_default = arg_info.default
        required = actual_default is ...
        help_text = arg_info.help
        cli_flag = name.upper()
        if required:
            actual_default = None
    elif default_val is inspect.Parameter.empty:
        # Positional with no default — required
        required = True
        actual_default = None
        help_text = None
        cli_flag = name.upper()
    else:
        # Plain Python default — not a typer annotation; skip
        return None

    choices = _extract_choices(annotation)
    is_path = _is_path_type(annotation)
    py_type = _stringify_annotation(annotation)

    return ParamSpec(
        name=name,
        cli_flag=cli_flag,
        py_type=py_type,
        required=required,
        default=actual_default,
        help=help_text,
        choices=choices,
        is_path=is_path,
    )


def _command_name(info: typer.models.CommandInfo) -> str:
    if info.name:
        return info.name
    cb = info.callback
    if cb is None:
        return "unknown"
    return cb.__name__.replace("_", "-")


def _build_command(
    info: typer.models.CommandInfo, parent_path: tuple[str, ...]
) -> CommandNode:
    name = _command_name(info)
    full_path = (*parent_path, name)
    cb = info.callback
    help_text = info.help or (cb.__doc__ or "").strip() if cb else ""

    params: list[ParamSpec] = []
    if cb is not None:
        try:
            sig = inspect.signature(cb)
        except (ValueError, TypeError):
            sig = None
        if sig is not None:
            try:
                type_hints = typing.get_type_hints(cb)
            except Exception:
                type_hints = {}
            for param_name, param in sig.parameters.items():
                resolved_annotation = type_hints.get(param_name, param.annotation)
                resolved_param = param.replace(annotation=resolved_annotation)
                ps = _build_param(param_name, resolved_param)
                if ps is not None:
                    params.append(ps)

    return CommandNode(
        name=name,
        full_path=full_path,
        help=help_text,
        callback=cb,
        params=params,
        is_destructive=is_destructive(full_path),
    )


def _build_group(
    typer_instance: typer.Typer,
    name: str,
    parent_path: tuple[str, ...],
) -> GroupNode:
    full_path = (*parent_path, name)
    help_text = typer_instance.info.help or ""

    children: list[GroupNode | CommandNode] = []

    for cmd_info in typer_instance.registered_commands:
        children.append(_build_command(cmd_info, full_path))

    for group_info in typer_instance.registered_groups:
        group_name = group_info.name or "unknown"
        sub_instance = group_info.typer_instance
        if sub_instance is not None:
            children.append(_build_group(sub_instance, group_name, full_path))

    return GroupNode(
        name=name,
        full_path=full_path,
        help=help_text,
        children=children,
    )


def discover(app: typer.Typer) -> GroupNode:
    """Walk typer.Typer structure and return a GroupNode tree."""
    root_help = app.info.help or "Virgil — autonomous BMad Phase 4 agent"
    children: list[GroupNode | CommandNode] = []

    for cmd_info in app.registered_commands:
        children.append(_build_command(cmd_info, ()))

    for group_info in app.registered_groups:
        group_name = group_info.name or "unknown"
        sub_instance = group_info.typer_instance
        if sub_instance is not None:
            children.append(_build_group(sub_instance, group_name, ()))

    return GroupNode(
        name="root",
        full_path=(),
        help=root_help,
        children=children,
    )


__all__ = ["discover"]
