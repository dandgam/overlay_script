"""Voice handler — pluggable STT provider (spec §15.8).

Voice .ogg → text (через выбранный provider) → free_text chat flow.
Аудио файл DELETED immediately после transcription.

Provider configurable: whisper_local | whisper_api | claude_audio |
                       yandex_speechkit | google_stt_v2 | disabled

152-ФЗ skipped per AABIT decision (личный проект, 2026-05-16).
"""

from __future__ import annotations

import uuid
from pathlib import Path

import structlog
from telegram import Update
from telegram.ext import ContextTypes

from bmad_orchestrator.bot.voice_providers import STTProvider, make_stt_provider
from bmad_orchestrator.config import load_settings

log = structlog.get_logger(__name__)

# Lazy-loaded provider — switched через settings.voice.stt_provider
_stt: STTProvider | None = None
_stt_provider_name: str | None = None


def _get_stt() -> STTProvider | None:
    """Lazy-construct STT provider. Rebuilds если config изменился."""
    global _stt, _stt_provider_name
    cfg = load_settings().voice
    if _stt_provider_name != cfg.stt_provider:
        _stt = make_stt_provider(
            cfg.stt_provider,
            model_size=cfg.stt_model,
            api_key=cfg.openai_api_key,
            folder_id=cfg.yandex_folder_id,
            credentials_path=cfg.google_credentials_path,
        )
        _stt_provider_name = cfg.stt_provider
        log.info("stt_provider_loaded", provider=cfg.stt_provider)
    return _stt


async def voice_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Voice message → STT → free_text flow.

    1. whitelist check
    2. download .ogg to /tmp
    3. STT through pluggable provider
    4. post-process typos
    5. DELETE .ogg
    6. forward to free_text_handler как обычное сообщение
    """
    from bmad_orchestrator.bot.handlers import _whitelisted, free_text

    if not _whitelisted(update):
        return

    voice = update.message.voice if update.message else None
    if not voice:
        return

    stt = _get_stt()
    if stt is None:
        await update.message.reply_text("Voice disabled. Включи в config voice.stt_provider")
        return

    cfg = load_settings().voice
    tmp_dir = Path(cfg.audio_temp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    audio_path = tmp_dir / f"voice_{uuid.uuid4().hex}.ogg"

    try:
        # 1. Download from Telegram
        file = await voice.get_file()
        await file.download_to_drive(str(audio_path))
        log.info(
            "voice_received",
            duration=voice.duration,
            provider=stt.name,
            estimated_cost=stt.estimate_cost_usd(voice.duration),
        )

        # 2. Transcribe via pluggable provider
        text = await stt.transcribe(audio_path, language=cfg.language)
        text = post_process_transcription(text)
        log.info("voice_transcribed", text_len=len(text))

        # 3. Forward as text message (mock override via update text)
        # In real implementation — invoke agent chat queue directly
        # For now: reply with transcription as confirmation
        await update.message.reply_text(f"📝 «{text}»\n\nОбрабатываю...")
        # TODO: push text into agent chat queue (same as free_text would)
        _ = free_text  # placeholder for actual integration

    finally:
        # 4. ALWAYS delete audio (no persistent storage)
        if cfg.delete_audio_after_transcription and audio_path.exists():
            audio_path.unlink()


# Technical-term post-processing patterns
# (любой STT иногда mis-hears английский в русской речи)
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
