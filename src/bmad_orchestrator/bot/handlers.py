"""Telegram handlers (spec §15.2, §15.3, §15.4).

Whitelist check + PII detector + forward to agent.
"""

from __future__ import annotations

from typing import Any

import structlog
from telegram import Update
from telegram.ext import ContextTypes

from bmad_orchestrator.bot.pii_detector import scrub_input, scrub_output
from bmad_orchestrator.config import load_settings

log = structlog.get_logger(__name__)


def _whitelisted(update: Update) -> bool:
    cfg = load_settings().telegram
    if not cfg.chat_id_whitelist:
        return False
    chat_id = update.effective_chat.id if update.effective_chat else None
    return chat_id in cfg.chat_id_whitelist


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _whitelisted(update):
        return
    await update.message.reply_text(
        "Привет! Я оркестратор bmad-auto-dev. Пиши свободно — пойму."
    )


async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _whitelisted(update):
        return
    msg = (
        "Я понимаю свободный русский. Примеры:\n"
        "• «запусти одиссей 1a» — стартую wave\n"
        "• «что сейчас?» — снимок состояния\n"
        "• «почему 1.10a долго?» — анализ worker'а\n"
        "• «дорого, на сонет» — переключу модель\n\n"
        "Опасные операции я уточняю кнопками."
    )
    await update.message.reply_text(msg)


async def status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _whitelisted(update):
        return
    # TODO: query agent for snapshot
    await update.message.reply_text("TODO: status snapshot")


async def stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _whitelisted(update):
        return
    # TODO: route to agent via chat queue with mode confirmation
    await update.message.reply_text("TODO: stop with inline confirmation")


async def model(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _whitelisted(update):
        return
    await update.message.reply_text("TODO: model raskladka")


async def budget(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _whitelisted(update):
        return
    await update.message.reply_text("TODO: budget snapshot")


async def free_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """PRIMARY handler — свободный текст → agent (spec §15.2)."""
    if not _whitelisted(update):
        return

    raw = update.message.text or ""
    scrubbed, pii_found = scrub_input(raw)
    if pii_found:
        # TODO: ask user to confirm via inline button
        log.info("pii_detected_in_input", chat=update.effective_chat.id)

    # TODO: push to agent's chat queue, await response
    response = "TODO: forward to agent"
    safe_response, _ = scrub_output(response)
    await update.message.reply_text(safe_response)


async def callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Inline button clicks (confirmation, choice selection)."""
    if not _whitelisted(update):
        return
    query = update.callback_query
    await query.answer()
    # TODO: route choice to agent
    await query.edit_message_text(f"TODO: handle callback {query.data}")
