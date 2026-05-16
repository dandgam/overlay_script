"""Telegram bot entrypoint (spec §15.1).

Запуск отдельным daemon'ом (systemd unit в S8 deploy):

    python -m bmad_orchestrator.bot.main

Smoke-проверка конфига без подключения к Telegram API:

    python -m bmad_orchestrator.bot.main --check-config
"""

from __future__ import annotations

import argparse
import os
import sys

import structlog

from bmad_orchestrator.bot.telegram_bot import build_application, run_bot
from bmad_orchestrator.config import load_settings

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


def main(argv: list[str] | None = None) -> int:
    os.umask(0o077)
    parser = argparse.ArgumentParser(prog="bmad-bot")
    parser.add_argument("--check-config", action="store_true", help="validate settings and exit")
    args = parser.parse_args(argv)

    if args.check_config:
        return _check_config()

    # Build first — sanity check (token, whitelist) до запуска polling.
    build_application()
    run_bot()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
