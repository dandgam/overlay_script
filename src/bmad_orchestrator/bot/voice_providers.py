"""Pluggable voice providers — STT + TTS interface (spec §15.8).

Каждый provider реализует один из 2 protocols:
  STTProvider — voice → text
  TTSProvider — text → voice

Switch via CLI/Telegram/config — см. config.py:VoiceConfig.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


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

    def __init__(self, model_size: str = "medium"):
        self.model_size = model_size
        self._model = None  # lazy

    async def transcribe(self, audio_path: Path, language: str = "ru") -> str:
        if self._model is None:
            import whisper

            self._model = whisper.load_model(self.model_size)
        # whisper.transcribe is sync — run in executor in real implementation
        result = self._model.transcribe(str(audio_path), language=language)  # type: ignore[attr-defined]
        return result["text"].strip()  # type: ignore[no-any-return]

    def estimate_cost_usd(self, duration_seconds: float) -> float:
        return 0.0  # локально, без денег


class WhisperAPISTT(STTProvider):
    """OpenAI Whisper API — $0.006/мин. Cross-border (USA)."""

    name = "whisper_api"

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def transcribe(self, audio_path: Path, language: str = "ru") -> str:
        # TODO: openai.audio.transcriptions.create(...)
        raise NotImplementedError

    def estimate_cost_usd(self, duration_seconds: float) -> float:
        return (duration_seconds / 60.0) * 0.006


class ClaudeAudioSTT(STTProvider):
    """Anthropic Claude with audio input (beta 2026). Cross-border (USA)."""

    name = "claude_audio"

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def transcribe(self, audio_path: Path, language: str = "ru") -> str:
        # TODO: anthropic.messages.create с base64 audio block
        raise NotImplementedError

    def estimate_cost_usd(self, duration_seconds: float) -> float:
        # Claude pricing для audio — see Anthropic docs 2026
        return (duration_seconds / 60.0) * 0.003  # approximate


class YandexSpeechKitSTT(STTProvider):
    """Yandex SpeechKit — best для русского. ₽1.50/мин = ~$0.015/мин."""

    name = "yandex_speechkit"

    def __init__(self, api_key: str, folder_id: str):
        self.api_key = api_key
        self.folder_id = folder_id

    async def transcribe(self, audio_path: Path, language: str = "ru") -> str:
        # TODO: yandex_cloud_speechkit_python or raw HTTPS
        raise NotImplementedError

    def estimate_cost_usd(self, duration_seconds: float) -> float:
        return (duration_seconds / 60.0) * 0.015


class GoogleSTT(STTProvider):
    """Google Speech-to-Text v2 — $0.024/мин."""

    name = "google_stt_v2"

    def __init__(self, credentials_path: str):
        self.credentials_path = credentials_path

    async def transcribe(self, audio_path: Path, language: str = "ru") -> str:
        # TODO: google.cloud.speech_v2
        raise NotImplementedError

    def estimate_cost_usd(self, duration_seconds: float) -> float:
        return (duration_seconds / 60.0) * 0.024


# ─── Provider factory ────────────────────────────────────────────────────────


def make_stt_provider(name: str, **kwargs) -> STTProvider | None:
    """Construct provider by name. Returns None если 'disabled'."""
    if name == "disabled":
        return None
    if name == "whisper_local":
        return WhisperLocalSTT(model_size=kwargs.get("model_size", "medium"))
    if name == "whisper_api":
        return WhisperAPISTT(api_key=kwargs["api_key"])
    if name == "claude_audio":
        return ClaudeAudioSTT(api_key=kwargs["api_key"])
    if name == "yandex_speechkit":
        return YandexSpeechKitSTT(api_key=kwargs["api_key"], folder_id=kwargs["folder_id"])
    if name == "google_stt_v2":
        return GoogleSTT(credentials_path=kwargs["credentials_path"])
    raise ValueError(f"Unknown STT provider: {name}")


def make_tts_provider(name: str, **kwargs) -> TTSProvider | None:
    """Construct TTS provider by name. Returns None если 'disabled'."""
    if name == "disabled":
        return None
    # TODO: implement OpenAI TTS, ElevenLabs, Yandex, Coqui local
    raise NotImplementedError(f"TTS provider {name} not implemented (v2 feature)")
