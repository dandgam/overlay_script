"""python-telegram-bot v22 application setup (spec §15.1).

Bot — отдельный daemon, прокси между Telegram API и orchestrator agent's chat queue.
Сам без LLM. Forwards free text + voice → agent, отправляет ответ обратно.
"""

from __future__ import annotations

from typing import Any

import structlog
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from bmad_orchestrator.bot import handlers
from bmad_orchestrator.bot.voice_handler import voice_handler
from bmad_orchestrator.config import load_settings

log = structlog.get_logger(__name__)


def build_application() -> Application[Any, Any, Any, Any, Any, Any]:
    settings = load_settings()
    if not settings.telegram.bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set in environment")
    if not settings.telegram.chat_id_whitelist:
        raise RuntimeError(
            "TELEGRAM_ALLOWED_CHAT_IDS empty — refusing to start with no whitelist (spec §15.6)"
        )

    app: Application[Any, Any, Any, Any, Any, Any] = (
        Application.builder().token(settings.telegram.bot_token).build()
    )

    # Slash shortcuts (§15.3)
    app.add_handler(CommandHandler("start", handlers.start))
    app.add_handler(CommandHandler("help", handlers.help_cmd))
    app.add_handler(CommandHandler("status", handlers.status))
    app.add_handler(CommandHandler("stop", handlers.stop))
    app.add_handler(CommandHandler("model", handlers.model))
    app.add_handler(CommandHandler("budget", handlers.budget))

    # Voice (§15.8) — perehvat ДО free_text, чтобы text-фильтр не съел voice update
    app.add_handler(MessageHandler(filters.VOICE, voice_handler))

    # Primary: free-text → agent (§15.2)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.free_text))

    # Inline button callbacks (§15.4)
    app.add_handler(CallbackQueryHandler(handlers.callback))

    return app


def run_bot() -> None:
    """Build + run polling (PTB v22 manages its own asyncio loop)."""
    app = build_application()
    log.info("telegram_bot_starting")
    app.run_polling()


__all__ = ["build_application", "run_bot"]
