"""NEW-1 — `--project <slug>` flag must beat the `ORCHESTRATOR_TARGET_PROJECT` env var.

Covers `_resolve_settings_for_project` (cli/main.py) and the `virgil run`
wiring that hands a registry-resolved Settings to `run_orchestrator`.

CLI precedence under test: `--project` flag > `config/projects.yaml` registry
> `ORCHESTRATOR_TARGET_PROJECT` env > Settings default.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from bmad_orchestrator.cli import app
from bmad_orchestrator.cli.main import _resolve_settings_for_project
from bmad_orchestrator.config import load_settings
from bmad_orchestrator.runtime.project_registry import (
    REGISTRY_ENV_VAR,
    ProjectEntry,
    ProjectNotFoundError,
    ProjectsRegistry,
    save_registry,
)

runner = CliRunner()


def _registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **projects: Path) -> Path:
    """Write a registry yaml with ``projects`` and point the env override at it."""
    reg = ProjectsRegistry(
        projects={
            slug: ProjectEntry(path=path, bmad_layout="bmm-v6")
            for slug, path in projects.items()
        }
    )
    reg_file = tmp_path / "projects.yaml"
    save_registry(reg, reg_file)
    monkeypatch.setenv(REGISTRY_ENV_VAR, str(reg_file))
    return reg_file


def _settings_with_env_target(env_path: Path):
    """Settings whose target_project mimics an `ORCHESTRATOR_TARGET_PROJECT` env bind."""
    return load_settings().model_copy(update={"target_project": env_path})


# ── unit — _resolve_settings_for_project ─────────────────────────────────────


def test_resolve_flag_with_registry_entry_overrides_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--project antares` resolves to the registry path, not the env-bound one."""
    antares = tmp_path / "Antares"
    antares.mkdir()
    _registry(tmp_path, monkeypatch, antares=antares)
    settings = _settings_with_env_target(tmp_path / "odyssey")

    resolved = _resolve_settings_for_project(settings, "antares", strict=True)

    assert resolved.target_project == antares
    # The env-bound original is left untouched (model_copy, not mutation).
    assert settings.target_project == tmp_path / "odyssey"


def test_resolve_flag_without_entry_raises_in_strict_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unregistered slug fails loud instead of silently using the env target."""
    _registry(tmp_path, monkeypatch, antares=tmp_path / "Antares")
    settings = _settings_with_env_target(tmp_path / "odyssey")

    with pytest.raises(ProjectNotFoundError, match="not found"):
        _resolve_settings_for_project(settings, "ghost", strict=True)


def test_resolve_flag_matching_settings_name_is_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Slug equal to the current target name returns settings without registry lookup."""
    # Registry intentionally empty — a no-op must not consult it.
    _registry(tmp_path, monkeypatch)
    settings = _settings_with_env_target(tmp_path / "odyssey")

    resolved = _resolve_settings_for_project(settings, "odyssey", strict=True)

    assert resolved is settings


def test_resolve_flag_without_entry_degrades_in_non_strict_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-strict callers (status snapshots) degrade gracefully on a registry miss."""
    _registry(tmp_path, monkeypatch, antares=tmp_path / "Antares")
    settings = _settings_with_env_target(tmp_path / "odyssey")

    resolved = _resolve_settings_for_project(settings, "ghost", strict=False)

    assert resolved.target_project == tmp_path / "odyssey"


# ── integration — virgil run ─────────────────────────────────────────────────


def test_run_project_flag_overrides_env_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`virgil run --project antares` with env=odyssey → run_orchestrator gets antares."""
    antares = tmp_path / "Antares"
    antares.mkdir()
    _registry(tmp_path, monkeypatch, antares=antares)
    monkeypatch.setenv("ORCHESTRATOR_TARGET_PROJECT", str(tmp_path / "odyssey"))

    captured: dict[str, object] = {}

    async def _fake_run_orchestrator(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(
        "bmad_orchestrator.cli.main.run_orchestrator", _fake_run_orchestrator
    )

    result = runner.invoke(
        app, ["run", "--project", "antares", "--wave", "1a", "--real"]
    )

    assert result.exit_code == 0, result.output
    settings = captured["settings"]
    assert settings is not None
    assert settings.target_project == antares  # type: ignore[attr-defined]


def test_run_unregistered_project_exits_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`virgil run --project ghost` fails with a clear message, not a silent run."""
    _registry(tmp_path, monkeypatch, antares=tmp_path / "Antares")
    monkeypatch.setenv("ORCHESTRATOR_TARGET_PROJECT", str(tmp_path / "odyssey"))

    spawned: dict[str, object] = {}

    async def _fake_run_orchestrator(**kwargs: object) -> None:
        spawned.update(kwargs)

    monkeypatch.setattr(
        "bmad_orchestrator.cli.main.run_orchestrator", _fake_run_orchestrator
    )

    result = runner.invoke(
        app, ["run", "--project", "ghost", "--wave", "1a", "--real"]
    )

    assert result.exit_code == 1
    assert "ghost" in result.output
    assert "not found" in result.output
    assert not spawned, "run_orchestrator must not be reached on an unregistered slug"


# ── regression — resolution is consistent across subcommands ─────────────────


def test_resolution_consistent_strict_and_non_strict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A registered slug resolves to the same path for run (strict) and status (lax).

    Guards against `--project` meaning different targets across subcommands.
    """
    antares = tmp_path / "Antares"
    antares.mkdir()
    _registry(tmp_path, monkeypatch, antares=antares)
    settings = _settings_with_env_target(tmp_path / "odyssey")

    strict = _resolve_settings_for_project(settings, "antares", strict=True)
    lax = _resolve_settings_for_project(settings, "antares", strict=False)

    assert strict.target_project == lax.target_project == antares
