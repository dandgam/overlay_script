"""Telegram handlers (spec §15.2, §15.3, §15.4).

Whitelist check + PII detection + forward to agent + inline confirmation.

Bot — прокси без LLM. Free-text → orchestrator agent's chat queue (через
`forward_to_agent`). Slash commands — local shortcuts. Inline buttons —
confirmation для destructive ops (§15.4).
"""

from __future__ import annotations

from typing import Any

import structlog
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bmad_orchestrator.bot.audit import record_telegram_event
from bmad_orchestrator.bot.pii_detector import scrub_input, scrub_output
from bmad_orchestrator.config import load_settings

log = structlog.get_logger(__name__)


# ── Whitelist ──────────────────────────────────────────────────────────────


def _whitelisted(update: Update) -> bool:
    """True iff chat_id is in TelegramConfig.chat_id_whitelist."""
    cfg = load_settings().telegram
    if not cfg.chat_id_whitelist:
        return False
    chat = update.effective_chat
    if chat is None:
        return False
    return chat.id in cfg.chat_id_whitelist


async def _deny(update: Update, kind: str) -> None:
    """Audit denial without responding (silently drop to avoid info-leak)."""
    chat = update.effective_chat
    record_telegram_event(
        direction="in",
        chat_id=chat.id if chat else None,
        message_type="denied",
        extra={"reason": "whitelist_miss", "kind": kind},
    )
    log.warning("telegram_denied", chat_id=chat.id if chat else None, kind=kind)


# ── Agent forwarding (placeholder; full wiring в S8 mock pilot) ────────────


async def forward_to_agent(text: str, *, chat_id: int | None, source: str) -> str:
    """Push user text into orchestrator's chat queue.

    Real wiring (S8): emit `USER_CHAT_MESSAGE` в `EventLoop` + await response
    через `HUMAN_RESPONSE`-keyed future. На S6 — синхронный stub, чтобы
    handlers были testable end-to-end без полного orchestrator-loop'а.
    """
    log.info("telegram_forward_to_agent", source=source, chat_id=chat_id, text_len=len(text))
    return f"(оркестратор не подключён — S8) принято: {text[:80]}"


# ── Slash commands (spec §15.3) ────────────────────────────────────────────


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _whitelisted(update):
        await _deny(update, "start")
        return
    msg = update.message
    if msg is None:
        return
    chat = update.effective_chat
    await msg.reply_text(
        "Привет! Я оркестратор bmad-auto-dev. Пиши свободно — пойму. "
        "Опасные операции я уточняю кнопками."
    )
    record_telegram_event(
        direction="in",
        chat_id=chat.id if chat else None,
        message_type="command",
        extra={"cmd": "start"},
    )


async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _whitelisted(update):
        await _deny(update, "help")
        return
    msg = update.message
    if msg is None:
        return
    body = (
        "Я понимаю свободный русский:\n"
        "• «запусти одиссей 1a, два воркера» — стартую wave\n"
        "• «что сейчас?» — снимок состояния\n"
        "• «почему 1.10a долго?» — анализ worker'а\n"
        "• «дорого, на сонет» — переключу модель\n"
        "• «слушай через яндекс» — сменю STT\n\n"
        "Slash: /status /stop /model /budget /projects /cancel"
    )
    await msg.reply_text(body)


async def status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _whitelisted(update):
        await _deny(update, "status")
        return
    msg = update.message
    if msg is None:
        return
    response = await forward_to_agent("/status", chat_id=_chat_id(update), source="slash")
    await _send_safe(msg, response, chat_id=_chat_id(update), message_type="command")


async def stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Destructive op — confirmation через inline buttons (§15.4)."""
    if not _whitelisted(update):
        await _deny(update, "stop")
        return
    msg = update.message
    if msg is None:
        return
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("мягко — дождаться workers", callback_data="stop:graceful"),
                InlineKeyboardButton("жёстко — kill сейчас", callback_data="stop:hard"),
            ],
            [InlineKeyboardButton("отмена", callback_data="stop:cancel")],
        ]
    )
    await msg.reply_text("Как остановить?", reply_markup=keyboard)


async def model(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _whitelisted(update):
        await _deny(update, "model")
        return
    msg = update.message
    if msg is None:
        return
    cfg = load_settings().models
    body = (
        f"Текущая раскладка:\n"
        f"• planner: {cfg.planner}\n"
        f"• reviewer: {cfg.reviewer}\n"
        f"• dev: {cfg.dev}\n"
        f"• routine: {cfg.routine}\n"
        f"• mechanical: {cfg.mechanical}\n"
        f"• fallback: {cfg.fallback}"
    )
    await _send_safe(msg, body, chat_id=_chat_id(update), message_type="command")


async def budget(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _whitelisted(update):
        await _deny(update, "budget")
        return
    msg = update.message
    if msg is None:
        return
    response = await forward_to_agent("/budget", chat_id=_chat_id(update), source="slash")
    await _send_safe(msg, response, chat_id=_chat_id(update), message_type="command")


# ── Primary: free-text ─────────────────────────────────────────────────────


async def free_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """PRIMARY handler — свободный текст → agent (§15.2)."""
    if not _whitelisted(update):
        await _deny(update, "text")
        return
    msg = update.message
    if msg is None:
        return
    raw = msg.text or ""
    _, pii_in = scrub_input(raw)
    chat_id = _chat_id(update)
    record_telegram_event(
        direction="in",
        chat_id=chat_id,
        message_type="text",
        original=raw,
        redacted=raw,
        pii_categories=pii_in,
    )
    if pii_in:
        log.info("pii_detected_in_input", chat_id=chat_id, categories=pii_in)

    response = await forward_to_agent(raw, chat_id=chat_id, source="text")
    await _send_safe(msg, response, chat_id=chat_id, message_type="text")


# ── Inline callbacks (spec §15.4) ──────────────────────────────────────────


async def callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _whitelisted(update):
        await _deny(update, "callback")
        return
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    data = query.data or ""
    chat_id = _chat_id(update)

    if data == "stop:graceful":
        await forward_to_agent("/stop graceful", chat_id=chat_id, source="callback")
        text = "Останавливаю мягко — дождусь текущих workers."
    elif data == "stop:hard":
        await forward_to_agent("/stop hard", chat_id=chat_id, source="callback")
        text = "Останавливаю жёстко — kill сейчас."
    elif data == "stop:cancel":
        text = "Отмена. Workers продолжают."
    else:
        await forward_to_agent(f"callback:{data}", chat_id=chat_id, source="callback")
        text = f"Принято: {data}"

    record_telegram_event(
        direction="in",
        chat_id=chat_id,
        message_type="callback",
        original=data,
        extra={"action": data},
    )
    safe, redacted = scrub_output(text)
    await query.edit_message_text(safe)
    record_telegram_event(
        direction="out",
        chat_id=chat_id,
        message_type="callback",
        original=text,
        redacted=safe,
        pii_categories=redacted,
    )


# ── helpers ────────────────────────────────────────────────────────────────


def _chat_id(update: Update) -> int | None:
    chat = update.effective_chat
    return chat.id if chat else None


async def _send_safe(
    msg: Any,
    text: str,
    *,
    chat_id: int | None,
    message_type: str,
) -> None:
    """Reply through output PII scrub + audit log."""
    safe, redacted = scrub_output(text)
    await msg.reply_text(safe)
    record_telegram_event(
        direction="out",
        chat_id=chat_id,
        message_type=message_type,
        original=text,
        redacted=safe,
        pii_categories=redacted,
    )


__all__ = [
    "budget",
    "callback",
    "forward_to_agent",
    "free_text",
    "help_cmd",
    "model",
    "start",
    "status",
    "stop",
]
