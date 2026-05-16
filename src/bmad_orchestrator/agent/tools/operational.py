"""Operational tools (spec §17 — 5 tools for chat-mode intent recognition).

start_wave, stop_orchestrator, set_model, schedule_reminder, set_voice_provider.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from claude_agent_sdk import tool

from bmad_orchestrator.agent.tools._common import (
    append_jsonl,
    error,
    get_settings,
    json_ok,
    now_iso,
    runs_dir,
)
from bmad_orchestrator.state import StateDB

_VALID_ROLES = {"planner", "reviewer", "dev", "routine", "mechanical", "fallback"}
_VALID_STT = {
    "whisper_local",
    "whisper_api",
    "claude_audio",
    "yandex_speechkit",
    "google_stt_v2",
    "disabled",
}
_VALID_TTS = {"disabled", "openai_tts", "elevenlabs", "yandex_speechkit", "coqui_local"}


def _op_event(event_type: str, **payload: Any) -> None:
    append_jsonl(
        runs_dir() / "operational.events.jsonl",
        {"event_type": event_type, "ts": now_iso(), **payload},
    )


@tool(
    "start_wave",
    "Start orchestrator on a wave. Used when user says «запусти 1a» in Telegram.",
    {"project": str, "wave": str, "max_parallel": int, "model": str},
)
async def start_wave(args: dict[str, Any]) -> dict[str, Any]:
    project = str(args.get("project", ""))
    wave = str(args.get("wave", ""))
    max_parallel = int(args.get("max_parallel", 2))
    model = str(args.get("model", "")) or None
    if not project or not wave:
        return error("require 'project' and 'wave'", code="invalid_arg")

    settings = get_settings()
    db = StateDB(settings.state_db)
    await db.init()
    sid = await db.create_session(project, wave, max_parallel=max_parallel)
    _op_event(
        "wave_started",
        session_id=sid,
        project=project,
        wave=wave,
        max_parallel=max_parallel,
        model=model,
    )
    return json_ok(
        {
            "session_id": sid,
            "project": project,
            "wave": wave,
            "max_parallel": max_parallel,
            "model": model,
            "run_handle": f"run-{sid}",
        }
    )


@tool(
    "stop_orchestrator",
    "Stop orchestrator. graceful=wait for workers, hard=kill immediately. ALWAYS confirm.",
    {"mode": str},
)
async def stop_orchestrator(args: dict[str, Any]) -> dict[str, Any]:
    mode = str(args.get("mode", "graceful"))
    if mode not in ("graceful", "hard"):
        return error(f"invalid mode: {mode!r}", code="invalid_arg")
    settings = get_settings()
    db_path = settings.state_db
    sessions_ended = 0
    if db_path.exists():
        from bmad_orchestrator.state import connect

        async with connect(db_path) as conn:
            cur = await conn.execute(
                "SELECT id FROM agent_session WHERE status='running' ORDER BY id"
            )
            ids = [int(row["id"]) async for row in cur]
            for sid in ids:
                await conn.execute(
                    "UPDATE agent_session SET status='stopped', ended_at=? WHERE id=?",
                    (now_iso(), sid),
                )
                sessions_ended += 1
            await conn.commit()
    _op_event("stop_orchestrator", mode=mode, sessions_ended=sessions_ended)
    return json_ok({"mode": mode, "sessions_ended": sessions_ended})


@tool(
    "set_model",
    "Change model assignment for a role (planner|reviewer|dev|routine|mechanical|fallback).",
    {"role": str, "model": str},
)
async def set_model(args: dict[str, Any]) -> dict[str, Any]:
    role = str(args.get("role", ""))
    model = str(args.get("model", ""))
    if role not in _VALID_ROLES:
        return error(f"invalid role: {role!r}", code="invalid_arg")
    if not model:
        return error("missing 'model'", code="invalid_arg")
    cfg_path = _models_config_path()
    cfg = _load_models_config(cfg_path)
    cfg[role] = model
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    _op_event("model_set", role=role, model=model)
    return json_ok({"role": role, "model": model, "config_path": str(cfg_path)})


def _models_config_path() -> Path:
    return get_settings().orchestrator_home / "_config" / "orchestrator-models.json"


def _load_models_config(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


@tool(
    "schedule_reminder",
    "Schedule a reminder to be sent at a future datetime (ISO-8601).",
    {"when_iso": str, "message": str},
)
async def schedule_reminder(args: dict[str, Any]) -> dict[str, Any]:
    when_iso = str(args.get("when_iso", ""))
    message = str(args.get("message", ""))
    if not when_iso or not message:
        return error("require 'when_iso' and 'message'", code="invalid_arg")
    try:
        when = datetime.fromisoformat(when_iso)
    except ValueError:
        return error(f"invalid when_iso: {when_iso!r}", code="invalid_arg")
    reminder_id = f"r-{abs(hash((when_iso, message))) % 100_000:05d}"
    _op_event(
        "reminder_scheduled",
        reminder_id=reminder_id,
        when_iso=when.isoformat(),
        message_preview=message[:80],
    )
    return json_ok(
        {
            "reminder_id": reminder_id,
            "when_iso": when.isoformat(),
            "message_length": len(message),
        }
    )


@tool(
    "set_voice_provider",
    "Switch voice STT/TTS provider. Activates на «слушай через яндекс», «голос на whisper».",
    {"channel": str, "provider": str},
)
async def set_voice_provider(args: dict[str, Any]) -> dict[str, Any]:
    channel = str(args.get("channel", ""))
    provider = str(args.get("provider", ""))
    if channel not in ("stt", "tts"):
        return error(f"invalid channel: {channel!r}", code="invalid_arg")
    allowed = _VALID_STT if channel == "stt" else _VALID_TTS
    if provider not in allowed:
        return error(
            f"invalid provider {provider!r} for channel {channel!r}", code="invalid_arg"
        )
    _op_event("voice_provider_set", channel=channel, provider=provider)
    return json_ok({"channel": channel, "provider": provider})


TOOLS = [
    start_wave,
    stop_orchestrator,
    set_model,
    schedule_reminder,
    set_voice_provider,
]


__all__ = [
    "TOOLS",
    "schedule_reminder",
    "set_model",
    "set_voice_provider",
    "start_wave",
    "stop_orchestrator",
]
