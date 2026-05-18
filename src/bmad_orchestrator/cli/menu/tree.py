"""MenuTree pydantic v2 schemas per spec §3.3."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class ParamSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    cli_flag: str
    py_type: str
    required: bool
    default: object | None
    help: str | None
    choices: list[str] | None
    is_path: bool = False
    is_secret: bool = False


class CommandNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["command"] = "command"
    name: str
    full_path: tuple[str, ...]
    help: str
    callback: object
    params: list[ParamSpec]
    is_destructive: bool = False


class GroupNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["group"] = "group"
    name: str
    full_path: tuple[str, ...]
    help: str
    children: list[GroupNode | CommandNode]


GroupNode.model_rebuild()

MenuTree = GroupNode

__all__ = ["CommandNode", "GroupNode", "MenuTree", "ParamSpec"]
