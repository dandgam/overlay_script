"""Telegram bot entrypoint (spec §15.1).

Запуск отдельным daemon'ом (systemd unit в S8 deploy):

    python -m bmad_orchestrator.bot.main

Smoke-проверка конфига без подключения к Telegram API:

    python -m bmad_orchestrator.bot.main --check-config
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

import structlog

from bmad_orchestrator.agent.run import SESSION_ENV_VAR
from bmad_orchestrator.bot.handlers import attach_state_db
from bmad_orchestrator.bot.telegram_bot import build_application, run_bot
from bmad_orchestrator.config import load_settings
from bmad_orchestrator.state.db import StateDB

log = structlog.get_logger(__name__)


def _check_config() -> int:
    settings = load_settings()
    tg = settings.telegram
    voice = settings.voice
    issues: list[str] = []
    if not tg.bot_token:
        issues.append("TELEGRAM_BOT_TOKEN не установлен")
    if not tg.chat_id_whitelist:
        issues.append("TELEGRAM_ALLOWED_CHAT_IDS пуст (whitelist обязателен)")
    if voice.stt_provider not in {"disabled", "whisper_local"} and not (
        voice.openai_api_key or voice.yandex_api_key or voice.google_credentials_path
    ):
        issues.append(f"voice.stt_provider={voice.stt_provider}, но нет credentials")

    if issues:
        for it in issues:
            print(f"  ✗ {it}", file=sys.stderr)
        return 1
    print(
        f"  ✓ bot_token: set (len={len(tg.bot_token or '')})\n"
        f"  ✓ chat_id_whitelist: {tg.chat_id_whitelist}\n"
        f"  ✓ stt_provider: {voice.stt_provider} (fallback={voice.stt_fallback})\n"
        f"  ✓ pii_redact: {tg.pii_redact}"
    )
    return 0


async def _attach_bridge() -> None:
    """FS8 NH1 — wire cross-process StateDB bridge using shared session.

    Resolution priority (mirrors ``agent.run._resolve_session``):
    1. Env ``BMAD_ORCHESTRATOR_SESSION_ID`` (orchestrator already created /
       exported one) → use it.
    2. ``StateDB.resolve_or_create_session(target_project=settings.target_project)``
       with ``wave=None`` so the bot picks up an orchestrator-created session
       regardless of the wave label. Falls through to INSERT only when the
       orchestrator has not started yet.

    On any DB error the bridge degrades to stub mode (handlers' default
    in-process behaviour) so the bot still boots.
    """
    settings = load_settings()
    target_project = str(settings.target_project)
    try:
        db = StateDB(db_path=settings.state_db)
        await db.init()

        env_value = os.environ.get(SESSION_ENV_VAR)
        session_id: int | None = None
        if env_value:
            try:
                candidate = int(env_value)
            except ValueError:
                log.warning(
                    "bot_session_env_invalid",
                    env_var=SESSION_ENV_VAR,
                    value=env_value,
                    fallback="resolve_or_create",
                )
            else:
                from bmad_orchestrator.agent.run import _session_exists

                if await _session_exists(db, candidate):
                    session_id = candidate
                    log.info(
                        "bot_session_from_env",
                        env_var=SESSION_ENV_VAR,
                        session_id=session_id,
                    )
                else:
                    log.warning(
                        "bot_session_env_stale",
                        env_var=SESSION_ENV_VAR,
                        value=env_value,
                        db_path=str(settings.state_db),
                        fallback="resolve_or_create",
                    )

        if session_id is None:
            session_id = await db.resolve_or_create_session(
                target_project=target_project,
                wave=None,
                max_parallel=settings.max_parallel_workers,
            )
            # FS9 R5 NH2: attach BEFORE setting env. If attach raises, env stays
            # clean — a subsequent retry of _attach_bridge re-resolves from
            # scratch. Old order set env first → an attach failure left a
            # "valid" env pointer + no actual bridge wired, silent desync.

        attach_state_db(db, session_id)
        # Only after attach succeeds: publish env for child processes.
        os.environ[SESSION_ENV_VAR] = str(session_id)
        log.info(
            "bot_state_db_attached",
            db_path=str(settings.state_db),
            session_id=session_id,
        )
    except Exception as exc:
        # FS9 H9: production launchers MUST set BMAD_REQUIRE_DB_BRIDGE=1 so a
        # broken DB bridge halts startup loudly instead of degrading to stub
        # mode (where cross-process state is silently desynced).
        require = os.environ.get("BMAD_REQUIRE_DB_BRIDGE", "").strip().lower() in {
            "1", "true", "yes",
        }
        if require:
            raise RuntimeError(
                f"BMAD_REQUIRE_DB_BRIDGE=1 but bot DB bridge failed "
                f"(db_path={settings.state_db}): {exc}. Refusing to start in "
                "stub mode."
            ) from exc
        log.warning(
            "bot_state_db_unavailable",
            db_path=str(settings.state_db),
            error=str(exc),
            fallback="stub_mode",
        )


def main(argv: list[str] | None = None) -> int:
    os.umask(0o077)
    parser = argparse.ArgumentParser(prog="bmad-bot")
    parser.add_argument("--check-config", action="store_true", help="validate settings and exit")
    args = parser.parse_args(argv)

    if args.check_config:
        return _check_config()

    # Build first — sanity check (token, whitelist) до запуска polling.
    build_application()
    # N6 — attach cross-process bridge before run_bot() so the very first
    # forward_to_agent call already has the StateDB binding.
    asyncio.run(_attach_bridge())
    run_bot()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
