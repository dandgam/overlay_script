"""Pluggable voice providers — STT + TTS interface (spec §15.8).

Каждый provider реализует один из 2 protocols:
  STTProvider — voice → text
  TTSProvider — text → voice

Switch via CLI/Telegram/config — см. config.py:VoiceConfig.

Fallback chain: `transcribe_with_fallback(primary, fallback)` пытается primary,
при исключении — пишет лог и пробует fallback. Спек §15.8.4.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)


class STTProvider(ABC):
    """Speech-to-Text interface."""

    name: str

    @abstractmethod
    async def transcribe(self, audio_path: Path, language: str = "ru") -> str: ...

    @abstractmethod
    def estimate_cost_usd(self, duration_seconds: float) -> float: ...


class TTSProvider(ABC):
    """Text-to-Speech interface (v2 feature)."""

    name: str

    @abstractmethod
    async def synthesize(self, text: str, output_path: Path, language: str = "ru") -> None: ...

    @abstractmethod
    def estimate_cost_usd(self, char_count: int) -> float: ...


# ─── STT implementations ─────────────────────────────────────────────────────


class WhisperLocalSTT(STTProvider):
    """OpenAI Whisper, локально. Apache 2.0, бесплатно, no cross-border."""

    name = "whisper_local"

    def __init__(self, model_size: str = "medium") -> None:
        self.model_size = model_size
        self._model: Any = None  # lazy

    async def transcribe(self, audio_path: Path, language: str = "ru") -> str:
        if self._model is None:
            import whisper

            self._model = whisper.load_model(self.model_size)
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: self._model.transcribe(str(audio_path), language=language),
        )
        text = result.get("text", "")
        return str(text).strip()

    def estimate_cost_usd(self, duration_seconds: float) -> float:
        return 0.0  # локально, без денег


class WhisperAPISTT(STTProvider):
    """OpenAI Whisper API — $0.006/мин. Cross-border (USA)."""

    name = "whisper_api"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    async def transcribe(self, audio_path: Path, language: str = "ru") -> str:
        # Реальный wiring: openai.audio.transcriptions.create(file=..., model="whisper-1")
        raise NotImplementedError("WhisperAPISTT wiring deferred to live integration")

    def estimate_cost_usd(self, duration_seconds: float) -> float:
        return (duration_seconds / 60.0) * 0.006


class ClaudeAudioSTT(STTProvider):
    """Anthropic Claude with audio input (beta 2026). Cross-border (USA)."""

    name = "claude_audio"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    async def transcribe(self, audio_path: Path, language: str = "ru") -> str:
        # anthropic.messages.create с audio content block (beta)
        raise NotImplementedError("ClaudeAudioSTT wiring deferred to beta-release")

    def estimate_cost_usd(self, duration_seconds: float) -> float:
        return (duration_seconds / 60.0) * 0.003


class YandexSpeechKitSTT(STTProvider):
    """Yandex SpeechKit — best для русского. ₽1.50/мин = ~$0.015/мин."""

    name = "yandex_speechkit"

    def __init__(self, api_key: str, folder_id: str) -> None:
        self.api_key = api_key
        self.folder_id = folder_id

    async def transcribe(self, audio_path: Path, language: str = "ru") -> str:
        raise NotImplementedError("YandexSpeechKitSTT wiring deferred to live integration")

    def estimate_cost_usd(self, duration_seconds: float) -> float:
        return (duration_seconds / 60.0) * 0.015


class GoogleSTT(STTProvider):
    """Google Speech-to-Text v2 — $0.024/мин."""

    name = "google_stt_v2"

    def __init__(self, credentials_path: str) -> None:
        self.credentials_path = credentials_path

    async def transcribe(self, audio_path: Path, language: str = "ru") -> str:
        raise NotImplementedError("GoogleSTT wiring deferred to live integration")

    def estimate_cost_usd(self, duration_seconds: float) -> float:
        return (duration_seconds / 60.0) * 0.024


# ─── Provider factory ────────────────────────────────────────────────────────


VALID_STT_NAMES: frozenset[str] = frozenset(
    {
        "disabled",
        "whisper_local",
        "whisper_api",
        "claude_audio",
        "yandex_speechkit",
        "google_stt_v2",
    }
)


def make_stt_provider(name: str, **kwargs: Any) -> STTProvider | None:
    """Construct provider by name. Returns None если 'disabled'."""
    if name == "disabled":
        return None
    if name == "whisper_local":
        return WhisperLocalSTT(model_size=kwargs.get("model_size", "medium"))
    if name == "whisper_api":
        api_key = kwargs.get("api_key")
        if not api_key:
            raise ValueError("whisper_api requires api_key")
        return WhisperAPISTT(api_key=str(api_key))
    if name == "claude_audio":
        api_key = kwargs.get("api_key")
        if not api_key:
            raise ValueError("claude_audio requires api_key")
        return ClaudeAudioSTT(api_key=str(api_key))
    if name == "yandex_speechkit":
        api_key = kwargs.get("api_key")
        folder_id = kwargs.get("folder_id")
        if not api_key or not folder_id:
            raise ValueError("yandex_speechkit requires api_key + folder_id")
        return YandexSpeechKitSTT(api_key=str(api_key), folder_id=str(folder_id))
    if name == "google_stt_v2":
        cred = kwargs.get("credentials_path")
        if not cred:
            raise ValueError("google_stt_v2 requires credentials_path")
        return GoogleSTT(credentials_path=str(cred))
    raise ValueError(f"Unknown STT provider: {name}")


def make_tts_provider(name: str, **kwargs: Any) -> TTSProvider | None:
    """Construct TTS provider by name. Returns None если 'disabled'."""
    if name == "disabled":
        return None
    raise NotImplementedError(f"TTS provider {name} not implemented (v2 feature)")


# ─── Fallback chain (spec §15.8.4) ──────────────────────────────────────────


async def transcribe_with_fallback(
    primary: STTProvider,
    fallback: STTProvider | None,
    audio_path: Path,
    language: str = "ru",
) -> tuple[str, str]:
    """Transcribe via primary; on exception fall back. Returns (text, used_name).

    used_name = "<primary.name>" если primary succeeded, иначе "<fallback.name>".
    Если оба упали — повторно raise оригинальное исключение primary.
    """
    try:
        text = await primary.transcribe(audio_path, language=language)
        return text, primary.name
    except Exception as exc:
        log.warning(
            "stt_primary_failed",
            provider=primary.name,
            error=type(exc).__name__,
            fallback=fallback.name if fallback else None,
        )
        if fallback is None:
            raise
        try:
            text = await fallback.transcribe(audio_path, language=language)
            return text, fallback.name
        except Exception:
            log.error("stt_fallback_failed", fallback=fallback.name)
            raise exc from None


__all__ = [
    "VALID_STT_NAMES",
    "ClaudeAudioSTT",
    "GoogleSTT",
    "STTProvider",
    "TTSProvider",
    "WhisperAPISTT",
    "WhisperLocalSTT",
    "YandexSpeechKitSTT",
    "make_stt_provider",
    "make_tts_provider",
    "transcribe_with_fallback",
]
