"""Load/save ``orchestrator-models.yaml`` per spec §16.4.

Файл живёт в ``<target>/_bmad-output/_config/orchestrator-models.yaml``:

    models:
      planner:    claude-opus-4-7
      reviewer:   claude-opus-4-7
      dev:        claude-sonnet-4-6
      routine:    claude-sonnet-4-6
      mechanical: claude-haiku-4-5
      fallback:   claude-haiku-4-5

Per-project preference. CLI/Telegram override на сессию.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from bmad_orchestrator.config import ModelConfig

CONFIG_FILENAME = "orchestrator-models.yaml"
ROLE_FIELDS: tuple[str, ...] = tuple(ModelConfig.model_fields.keys())


def config_path(target_project: Path) -> Path:
    """Default config file path inside target project."""
    return target_project / "_bmad-output" / "_config" / CONFIG_FILENAME


def load_models(path: Path | None) -> ModelConfig:
    """Read ModelConfig from YAML. Missing file → defaults (spec §16.3)."""
    if path is None or not path.is_file():
        return ModelConfig()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        return ModelConfig()
    section = raw.get("models", raw)
    if not isinstance(section, dict):
        return ModelConfig()
    cleaned = {k: v for k, v in section.items() if k in ROLE_FIELDS and isinstance(v, str)}
    return ModelConfig(**cleaned)


def save_models(path: Path, cfg: ModelConfig) -> None:
    """Write ModelConfig to YAML. Creates parent dirs if missing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"models": cfg.model_dump()}
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def apply_overrides(cfg: ModelConfig, **overrides: str | None) -> ModelConfig:
    """Return a new ModelConfig with non-None overrides applied.

    Используется и CLI (``--planner-model opus``), и Telegram /model handler.
    """
    data = cfg.model_dump()
    for role, value in overrides.items():
        if value is None:
            continue
        if role not in ROLE_FIELDS:
            raise ValueError(f"unknown role: {role!r} (expected one of {ROLE_FIELDS})")
        data[role] = value
    return ModelConfig(**data)


def apply_all(cfg: ModelConfig, model: str | None) -> ModelConfig:
    """Set all six roles to the same model — ``bmad-orchestrator run --model opus``."""
    if model is None:
        return cfg
    data = dict.fromkeys(ROLE_FIELDS, model)
    return ModelConfig(**data)


__all__ = [
    "CONFIG_FILENAME",
    "ROLE_FIELDS",
    "apply_all",
    "apply_overrides",
    "config_path",
    "load_models",
    "save_models",
]
