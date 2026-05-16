"""Voice handler — pluggable STT provider (spec §15.8).

Voice .ogg → text (через выбранный provider) → free_text chat flow.
Аудио файл DELETED immediately после transcription (no persistent storage).

Provider configurable: whisper_local | whisper_api | claude_audio |
                       yandex_speechkit | google_stt_v2 | disabled

Fallback chain: при ошибке primary → автоматически попробовать
`settings.voice.stt_fallback`. Уведомление в чат пишется отдельным сообщением.

152-ФЗ skipped per AABIT decision (личный проект, 2026-05-16).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import structlog
from telegram import Update
from telegram.ext import ContextTypes

from bmad_orchestrator.bot.audit import record_telegram_event
from bmad_orchestrator.bot.voice_providers import (
    STTProvider,
    make_stt_provider,
    transcribe_with_fallback,
)
from bmad_orchestrator.config import VoiceConfig, load_settings

log = structlog.get_logger(__name__)


# Lazy-loaded provider cache — switched при изменении settings.voice.stt_provider.
_stt_cache: dict[str, STTProvider | None] = {}


def _provider_kwargs(cfg: VoiceConfig) -> dict[str, Any]:
    return {
        "model_size": cfg.stt_model,
        "api_key": cfg.openai_api_key or cfg.yandex_api_key,
        "folder_id": cfg.yandex_folder_id,
        "credentials_path": cfg.google_credentials_path,
    }


def get_stt(cfg: VoiceConfig) -> STTProvider | None:
    """Build (or fetch cached) primary STT provider per config."""
    name = cfg.stt_provider
    if name not in _stt_cache:
        try:
            _stt_cache[name] = make_stt_provider(name, **_provider_kwargs(cfg))
        except Exception as exc:
            log.warning("stt_provider_init_failed", provider=name, error=str(exc))
            _stt_cache[name] = None
    return _stt_cache[name]


def get_fallback(cfg: VoiceConfig) -> STTProvider | None:
    """Build (or fetch cached) fallback STT provider (whisper_local by default)."""
    name = cfg.stt_fallback
    if name == cfg.stt_provider:
        return None
    key = f"__fallback__{name}"
    if key not in _stt_cache:
        try:
            _stt_cache[key] = make_stt_provider(name, **_provider_kwargs(cfg))
        except Exception:
            _stt_cache[key] = None
    return _stt_cache[key]


def reset_provider_cache() -> None:
    """Clear cached providers — для тестов и `set_voice_provider` tool."""
    _stt_cache.clear()


# ── Technical-term post-processing ─────────────────────────────────────────

TYPO_PATTERNS: dict[str, str] = {
    "карго тошнол": "Cargo.toml",
    "карго томл": "Cargo.toml",
    "вокер": "worker",
    "ворктри": "worktree",
    "бмад": "bmad",
    "пе ар": "PR",
    "пиар": "PR",
    "ципиай": "CI",
    "сипиай": "CI",
    "сонет": "sonnet",
    "опус": "opus",
    "хайку": "haiku",
}


def post_process_transcription(text: str) -> str:
    """Fix common STT mis-hearings for technical RU+EN речи."""
    result = text
    for typo, correct in TYPO_PATTERNS.items():
        result = result.replace(typo, correct)
    return result


# ── Telegram handler ───────────────────────────────────────────────────────


async def voice_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Voice message → STT → free_text flow.

    1. whitelist check
    2. download .ogg to tmp dir
    3. STT через pluggable provider + fallback chain
    4. post-process typos
    5. DELETE .ogg (no persistent storage)
    6. forward результат в orchestrator chat queue
    """
    # Late import предотвращает циклический импорт handlers↔voice_handler.
    from bmad_orchestrator.bot.handlers import _whitelisted, forward_to_agent

    if not _whitelisted(update):
        return

    message = update.message
    voice = message.voice if message else None
    if not message or not voice:
        return

    cfg = load_settings().voice
    primary = get_stt(cfg)
    if primary is None:
        await message.reply_text("Голос выключен. Включи в config voice.stt_provider.")
        return

    fallback = get_fallback(cfg)
    tmp_dir = Path(cfg.audio_temp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    audio_path = tmp_dir / f"voice_{uuid.uuid4().hex}.ogg"

    chat_id = update.effective_chat.id if update.effective_chat else None
    raw_dur = voice.duration
    if raw_dur is None:
        duration_s = 0.0
    elif hasattr(raw_dur, "total_seconds"):
        duration_s = raw_dur.total_seconds()
    else:
        duration_s = float(raw_dur)
    try:
        file = await voice.get_file()
        await file.download_to_drive(str(audio_path))
        log.info(
            "voice_received",
            duration=duration_s,
            provider=primary.name,
            estimated_cost=primary.estimate_cost_usd(duration_s),
        )

        text, used = await transcribe_with_fallback(
            primary, fallback, audio_path, language=cfg.language
        )
        text = post_process_transcription(text)
        record_telegram_event(
            direction="in",
            chat_id=chat_id,
            message_type="voice",
            original=text,
            redacted=text,
            extra={
                "duration_s": duration_s,
                "provider": used,
                "cost_usd": primary.estimate_cost_usd(duration_s)
                if used == primary.name
                else (fallback.estimate_cost_usd(duration_s) if fallback else 0.0),
            },
        )

        if used != primary.name:
            await message.reply_text(
                f"{primary.name} недоступен — переключилась на {used}."
            )
        await message.reply_text(f"📝 «{text}»\n\nОбрабатываю...")
        await forward_to_agent(text, chat_id=chat_id, source="voice")

    finally:
        if cfg.delete_audio_after_transcription and audio_path.exists():
            audio_path.unlink()


__all__ = [
    "TYPO_PATTERNS",
    "get_fallback",
    "get_stt",
    "post_process_transcription",
    "reset_provider_cache",
    "voice_handler",
]
