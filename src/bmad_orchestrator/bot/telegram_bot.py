"""python-telegram-bot v22 application setup (spec §15.1).

Bot — отдельный daemon, прокси между Telegram API и orchestrator agent's chat queue.
Сам без LLM. Forwards free text → agent, отправляет ответ обратно.
"""

from __future__ import annotations

import structlog
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from bmad_orchestrator.config import load_settings

log = structlog.get_logger(__name__)


def build_application() -> Application:
    settings = load_settings()
    if not settings.telegram.bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set in environment")

    app = (
        Application.builder()
        .token(settings.telegram.bot_token)
        .build()
    )

    # Slash shortcuts (optional)
    from bmad_orchestrator.bot import handlers

    app.add_handler(CommandHandler("start", handlers.start))
    app.add_handler(CommandHandler("help", handlers.help_cmd))
    app.add_handler(CommandHandler("status", handlers.status))
    app.add_handler(CommandHandler("stop", handlers.stop))
    app.add_handler(CommandHandler("model", handlers.model))
    app.add_handler(CommandHandler("budget", handlers.budget))

    # Primary: free-text → agent (spec §15.2)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.free_text))

    # Inline button callbacks (spec §15.4)
    app.add_handler(CallbackQueryHandler(handlers.callback))

    return app


async def run_bot() -> None:
    app = build_application()
    log.info("telegram_bot_starting")
    await app.run_polling()
