"""Operational tools (spec §17 — 4 tools for chat-mode intent recognition).

start_wave, stop_orchestrator, set_model, schedule_reminder.
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool


@tool(
    "start_wave",
    "Start orchestrator on a wave. Used when user says «запусти 1a» in Telegram.",
    {"project": str, "wave": str, "max_parallel": int, "model": str},
)
async def start_wave(args: dict[str, Any]) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": "TODO: start wave"}]}


@tool(
    "stop_orchestrator",
    "Stop orchestrator. graceful=wait for workers, hard=kill immediately. ALWAYS confirm via inline button.",
    {"mode": str},  # graceful | hard
)
async def stop_orchestrator(args: dict[str, Any]) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": "TODO: stop"}]}


@tool(
    "set_model",
    "Change model assignment for a role (planner|reviewer|dev|routine|mechanical|fallback).",
    {"role": str, "model": str},
)
async def set_model(args: dict[str, Any]) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": "TODO: set model"}]}


@tool(
    "schedule_reminder",
    "Schedule a reminder to be sent at a future datetime.",
    {"when_iso": str, "message": str},
)
async def schedule_reminder(args: dict[str, Any]) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": "TODO: schedule reminder"}]}


@tool(
    "set_voice_provider",
    "Switch voice STT/TTS provider. Activates на «слушай через яндекс», «голос на whisper», «отключи голос».",
    {"channel": str, "provider": str},  # channel: stt | tts; provider: whisper_local | whisper_api | yandex_speechkit | claude_audio | google_stt_v2 | disabled
)
async def set_voice_provider(args: dict[str, Any]) -> dict[str, Any]:
    """Spec §15.8 + §16.5 pluggable voice."""
    return {"content": [{"type": "text", "text": "TODO: set voice provider"}]}
