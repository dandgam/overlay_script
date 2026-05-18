"""Centralized configuration.

См. spec §16 (model selection) + §11 (stack) + §8 (budget).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from bmad_orchestrator.agent.betas import ANTHROPIC_BETA_HEADERS

# N1 (FS6) — worker spawn defaults live here, not in runtime/worker_spawn.py,
# so other modules can import them without triggering the runtime ↔ agent.tools
# import chain (worker_spawn → agent.tools._common → agent.tools.__init__ →
# agent.tools.spawn → worker_spawn). Spec §5.6.1 Option A.
DEFAULT_MODEL: str = "claude-sonnet-4-6"
DEFAULT_BUDGET_CAP_USD: float = 30.0


class ModelConfig(BaseModel):
    """Per-role model routing. См. spec §16.3."""

    planner: str = "claude-opus-4-7"
    reviewer: str = "claude-opus-4-7"
    dev: str = "claude-sonnet-4-6"
    routine: str = "claude-sonnet-4-6"
    mechanical: str = "claude-haiku-4-5"
    fallback: str = "claude-haiku-4-5"


class BudgetConfig(BaseModel):
    """Two-tier budget caps. См. spec §8."""

    story_alarm_usd: float = 30.0
    story_halt_usd: float = 50.0
    batch_alarm_usd: float = 200.0
    batch_halt_usd: float = 300.0
    daily_limit_usd: float = 500.0


class TelegramConfig(BaseModel):
    """Telegram bot wiring. См. spec §15."""

    bot_token: str | None = None
    chat_id_whitelist: list[int] = Field(default_factory=list)
    pii_redact: bool = True


class VoiceConfig(BaseModel):
    """Pluggable voice providers (STT + TTS). См. spec §15.8.

    Каждый provider может быть переключён через CLI / Telegram / config file.
    """

    # STT (speech-to-text) — голос → текст
    stt_provider: Literal[
        "whisper_local",       # default, бесплатно, локально, no cross-border
        "whisper_api",         # OpenAI Whisper API, $0.006/мин
        "claude_audio",        # Anthropic Claude with audio (beta 2026)
        "yandex_speechkit",    # ₽1.50/мин, best for RU
        "google_stt_v2",       # $0.024/мин
        "disabled",            # voice handler не активен
    ] = "whisper_local"

    stt_model: str = "medium"  # для whisper: tiny/base/small/medium/large

    # TTS (text-to-speech) — текст → голос (v2 feature, OUT в MVP)
    tts_provider: Literal[
        "disabled",            # default — текстовые ответы
        "openai_tts",          # $0.015/1K char
        "elevenlabs",          # premium quality для RU
        "yandex_speechkit",    # natural RU
        "coqui_local",         # бесплатно локально
    ] = "disabled"

    # Fallback при ошибке primary provider
    stt_fallback: Literal[
        "whisper_local", "whisper_api", "disabled"
    ] = "whisper_local"

    # API keys для облачных провайдеров
    openai_api_key: str | None = None
    elevenlabs_api_key: str | None = None
    yandex_api_key: str | None = None
    yandex_folder_id: str | None = None
    google_credentials_path: str | None = None

    # Language hint (помогает точности всем providers)
    language: str = "ru"

    # Storage policy (152-ФЗ — skip для личного проекта, но pattern сохраняем)
    delete_audio_after_transcription: bool = True
    audio_temp_dir: str = "/tmp/bmad_voice"  # noqa: S108 — short-lived audio chunks, overridable via env


class Settings(BaseSettings):
    """Top-level environment-driven config.

    BAD-compatible env vars (alias names for migration):
      MAX_PARALLEL_STORIES   → max_parallel_workers
      MODEL_STANDARD         → models.dev / models.routine
      MODEL_QUALITY          → models.planner / models.reviewer
      WORKTREE_BASE_PATH     → worktree_base
    Они tradicionally без ORCHESTRATOR_ prefix.
    """

    model_config = SettingsConfigDict(
        env_prefix="ORCHESTRATOR_",
        env_file=".env",
        env_nested_delimiter="__",
    )

    target_project: Path = Path("/home/server/odyssey")
    orchestrator_home: Path = Path("/home/server/bmad-orchestrator")
    state_db: Path = Path("./state.db")

    # Embedded skills overlay root (spec_embed_phase45_with_selflearning §4 E3).
    # Workers receive a copy of `<skills_resolution_root>/upstream/` overlaid by
    # `customize/` in their worktree's `.claude/skills/`. Default = orchestrator's
    # `skills/` dir; override via ORCHESTRATOR_SKILLS_RESOLUTION_ROOT for tests.
    skills_resolution_root: Path = Path("/home/server/bmad-orchestrator/skills")

    # BMad-canonical artifact location (NOT _bmad/planning-artifacts — that was wrong)
    # _bmad/         = framework itself
    # _bmad-output/  = generated artifacts (planning + implementation)
    artifacts_dir_name: str = "_bmad-output"

    # Worktree layout (BAD default = .worktrees inside repo;
    # ours по handoff §6.2 = sibling /home/server/<proj>-wt-N)
    worktree_layout: Literal["sibling", "nested"] = "sibling"

    max_parallel_workers: int = 3  # matches BAD MAX_PARALLEL_STORIES default
    anthropic_api_key: str | None = None

    models: ModelConfig = Field(default_factory=ModelConfig)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)

    # Anthropic beta headers (§11.1, all mandatory) — canonical в agent/betas.py
    beta_headers: list[str] = Field(default_factory=lambda: list(ANTHROPIC_BETA_HEADERS))

    # Auto-elicitation engine — path to YAML policy. None → fallback to
    # examples/elicitation-policy.example.yaml at engine load time.
    elicitation_policy_path: Path | None = None

    # Supervisor LLM-loop — path to YAML policy. None → fallback to
    # config/supervisor-policy.yaml at engine load time.
    supervisor_policy_path: Path | None = None

    # Self-learning consolidation loop — path to YAML config. None → fallback to
    # config/self-learning.yaml at subscriber load time.
    self_learning_policy_path: Path | None = None

    # BMad Phase 4 canonical workflows — auto-trigger flags
    # If sprint-status.yaml отсутствует на старте wave — попытаться запустить
    # `bmad-sprint-planning` skill для генерации. False → fail loudly.
    auto_init_sprint_status: bool = True

    # Phase 4 hardening #3 — banned-phrase linter.
    # None → fallback to skills/policy/banned-phrases.yaml (relative to
    # orchestrator_home). Set to an absolute path to load a project-specific
    # override that EXTENDS (not replaces) the default phrase list.
    banned_phrases_path: Path | None = None

    locale: str = "ru"


def load_settings() -> Settings:
    """Load settings honoring env + .env file."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    settings = Settings()
    if api_key:
        settings.anthropic_api_key = api_key
    return settings
