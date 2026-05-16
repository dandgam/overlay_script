"""Telegram handlers (spec §15.2, §15.3, §15.4 + FS4 B9 cross-process bridge).

Whitelist check + PII detection + forward to agent + inline confirmation.

Bot — прокси без LLM. Free-text → orchestrator agent's chat queue (через
`forward_to_agent`). Slash commands — local shortcuts. Inline buttons —
confirmation для destructive ops (§15.4).

FS4 B9 — Bridge modes (priority order, first-match wins):

1. **State-DB bridge** (cross-process — bot + agent в разных процессах) —
   `attach_state_db(db, session_id)`. `forward_to_agent` пишет
   `human_query` row, polls `human_response` rows by corr_id. Latency ~poll
   interval (~50ms by default).
2. **In-process EventLoop bridge** — `attach_event_loop(bus)`. Bot и agent
   в одном asyncio loop'е. Synchronous future-resolve через `deliver_human_response`.
3. **Stub-mode** — neither wired. Echo placeholder. Tests can run without
   either backend.

Per-chat FIFO `_HUMAN_RESPONSES: dict[chat_id, list[(corr_id, Future)]]` —
multiple concurrent requests на один chat матчатся по `corr_id`. Caps:
- `_PER_CHAT_CAP = 5` — single chat нельзя залить (single-chat flood DoS).
- `_INFLIGHT_CAP = 100` — global hard limit across all chats.

Bridge-poisoning защита (SECURITY): `_poll_state_db_response` resolves ONLY
its own `(chat_id, corr_id)` row. Foreign rows re-enqueued (FIFO preserved)
with a 3-attempt cap per `row_corr` so an orphan can't ping-pong forever.
"""

from __future__ import annotations

import asyncio
import secrets
from typing import Any

import structlog
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bmad_orchestrator.bot.audit import record_telegram_event
from bmad_orchestrator.bot.pii_detector import scrub_input, scrub_output
from bmad_orchestrator.config import load_settings
from bmad_orchestrator.runtime.event_loop import EventLoop, EventType
from bmad_orchestrator.state.db import StateDB

log = structlog.get_logger(__name__)


# ── EventLoop / state-db bridge (FS4 B9) ──────────────────────────────────
#
# Bot и orchestrator могут жить в:
#   а) одном процессе — связка через in-memory EventLoop;
#   б) разных процессах — связка через `state.db.event_queue` (см.
#      StateDB.enqueue_human_query / claim_next_event_of_type).
#
# Bot никогда не вызывает LLM — это прокси с whitelist / PII / FIFO.
_BUS: EventLoop | None = None
_STATE_DB: StateDB | None = None
_SESSION_ID: int | None = None

_HUMAN_RESPONSES: dict[int, list[tuple[str, asyncio.Future[str]]]] = {}
_RESPONSES_LOCK: asyncio.Lock | None = None
_INFLIGHT_CAP: int = 100
_PER_CHAT_CAP: int = 5
_POLL_INTERVAL_SEC: float = 0.05  # 50ms — keeps cross-process bridge under 500ms p99
_MAX_REENQUEUE_ATTEMPTS: int = 3


def _get_lock() -> asyncio.Lock:
    """Lazy-init lock bound to running loop. Tests reset via reset_for_test()."""
    global _RESPONSES_LOCK
    if _RESPONSES_LOCK is None:
        _RESPONSES_LOCK = asyncio.Lock()
    return _RESPONSES_LOCK


def reset_for_test() -> None:
    """Test helper — clear in-flight state + lock binding between tests."""
    global _RESPONSES_LOCK
    _HUMAN_RESPONSES.clear()
    _RESPONSES_LOCK = None


def attach_event_loop(bus: EventLoop | None) -> None:
    """Wire bot handlers to the orchestrator's EventLoop instance.

    Call from `agent.run.run_orchestrator` before launching the bot daemon.
    `bus=None` resets to stub mode (для unit-тестов).
    """
    global _BUS
    _BUS = bus


def attach_state_db(db: StateDB | None, session_id: int | None) -> None:
    """Wire bot handlers to a shared SQLite state DB for cross-process bridge.

    When set, ``forward_to_agent`` writes a ``human_query`` row instead of
    emitting on the in-memory bus, and polls ``human_response`` rows by
    corr_id. ``db=None`` resets state-db mode.
    """
    global _STATE_DB, _SESSION_ID
    _STATE_DB = db
    _SESSION_ID = session_id


def _inflight_count() -> int:
    """Sum of pending futures across all chats."""
    return sum(len(v) for v in _HUMAN_RESPONSES.values())


def deliver_human_response(chat_id: int, corr_id: str, text: str) -> bool:
    """Resolve a pending future identified by (chat_id, corr_id).

    Synchronous — asyncio single-threadedness guarantees the mutation runs
    atomically between awaits. Returns True iff a matching pending future
    existed (and was just resolved).
    """
    pending = _HUMAN_RESPONSES.get(chat_id)
    if not pending:
        return False
    for i, (cid, fut) in enumerate(pending):
        if cid == corr_id:
            del pending[i]
            if not pending:
                _HUMAN_RESPONSES.pop(chat_id, None)
            if not fut.done():
                fut.set_result(text)
            return True
    return False


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


async def forward_to_agent(
    text: str,
    *,
    chat_id: int | None,
    source: str,
    timeout: float = 30.0,
) -> str:
    """Push user text into orchestrator's chat queue (FS4 B9 multi-bridge).

    Resolution order:
        1. State-DB bridge (cross-process) if attached.
        2. EventLoop bus (in-process) if attached.
        3. Stub echo (neither attached).

    A correlation id (`corr_id`, 16 hex chars) is generated per call and travels
    with the request payload so the response can be matched even when multiple
    requests for the same chat are in-flight concurrently.

    Two caps guard against DoS:
    - Per-chat cap (``_PER_CHAT_CAP``) — one chatty/compromised user can't
      monopolise the queue.
    - Global cap (``_INFLIGHT_CAP``) — total in-flight across all chats.
    Either tripping returns ``"queue_full"`` synchronously.
    """
    bridge: str
    if _STATE_DB is not None and _SESSION_ID is not None and chat_id is not None:
        bridge = "state_db"
    elif _BUS is not None and chat_id is not None:
        bridge = "bus"
    else:
        bridge = "stub"

    log.info(
        "telegram_forward_to_agent",
        source=source,
        chat_id=chat_id,
        text_len=len(text),
        bridge=bridge,
        inflight=_inflight_count(),
    )

    if bridge == "stub" or chat_id is None:
        return f"(оркестратор не подключён) принято: {text[:80]}"

    lock = _get_lock()
    loop = asyncio.get_running_loop()
    future: asyncio.Future[str] = loop.create_future()
    corr_id = secrets.token_hex(8)

    async with lock:
        if _inflight_count() >= _INFLIGHT_CAP:
            log.warning(
                "telegram_inflight_cap_reached",
                chat_id=chat_id,
                cap=_INFLIGHT_CAP,
                bridge=bridge,
            )
            return "queue_full"
        per_chat = len(_HUMAN_RESPONSES.get(chat_id, []))
        if per_chat >= _PER_CHAT_CAP:
            log.warning(
                "telegram_per_chat_cap_reached",
                chat_id=chat_id,
                cap=_PER_CHAT_CAP,
                bridge=bridge,
            )
            return "queue_full"
        _HUMAN_RESPONSES.setdefault(chat_id, []).append((corr_id, future))

    try:
        if bridge == "state_db":
            assert _STATE_DB is not None and _SESSION_ID is not None
            await _STATE_DB.enqueue_human_query(
                _SESSION_ID, chat_id, text, corr_id
            )
            poll_task = asyncio.create_task(
                _poll_state_db_response(chat_id, corr_id, timeout),
                name=f"poll_human_response_{corr_id}",
            )
            try:
                return await asyncio.wait_for(future, timeout=timeout)
            finally:
                poll_task.cancel()
        else:
            assert _BUS is not None
            await _BUS.emit(
                EventType.HUMAN_QUERY,
                chat_id=chat_id,
                corr_id=corr_id,
                text=text,
                source=source,
            )
            return await asyncio.wait_for(future, timeout=timeout)
    except TimeoutError:
        return f"(агент не ответил за {timeout:.0f}s — повтори)"
    finally:
        async with lock:
            _drop_pending(chat_id, corr_id)


async def _poll_state_db_response(chat_id: int, corr_id: str, timeout: float) -> None:
    """Drain ``human_response`` rows; resolve **only** our own (chat_id, corr_id).

    SECURITY — bridge-poisoning guard: another chat's row MUST NOT be resolved
    by this task. If a foreign row surfaces (claimed before its rightful
    polling task wakes), it is re-enqueued with a per-corr attempt cap so an
    orphan with no rightful owner can't ping-pong forever.

    Cooperative: scheduled by ``forward_to_agent`` and cancelled in its
    ``finally`` once the future resolves (success / timeout).
    """
    assert _STATE_DB is not None and _SESSION_ID is not None

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    re_enqueue_attempts: dict[str, int] = {}
    while loop.time() < deadline:
        row = await _STATE_DB.claim_next_event_of_type(_SESSION_ID, "human_response")
        if row is None:
            await asyncio.sleep(_POLL_INTERVAL_SEC)
            continue
        payload = row.get("payload") or {}
        row_chat = payload.get("chat_id")
        row_corr = payload.get("corr_id")
        row_text = str(payload.get("text", ""))
        if not isinstance(row_chat, int) or not isinstance(row_corr, str):
            log.warning("human_response_malformed_payload")
            continue
        if row_chat == chat_id and row_corr == corr_id:
            deliver_human_response(row_chat, row_corr, row_text)
            return
        attempts = re_enqueue_attempts.get(row_corr, 0)
        if attempts >= _MAX_REENQUEUE_ATTEMPTS:
            log.warning(
                "human_response_orphan_dropped",
                attempts=attempts,
                row_chat=row_chat,
            )
            continue
        re_enqueue_attempts[row_corr] = attempts + 1
        await _STATE_DB.enqueue_human_response(
            _SESSION_ID, row_chat, row_text, row_corr
        )
        await asyncio.sleep(_POLL_INTERVAL_SEC)


def _drop_pending(chat_id: int, corr_id: str) -> None:
    """Remove (corr_id, future) from the per-chat FIFO. No-op if already gone."""
    pending = _HUMAN_RESPONSES.get(chat_id)
    if not pending:
        return
    for i, (cid, _) in enumerate(pending):
        if cid == corr_id:
            del pending[i]
            break
    if not pending:
        _HUMAN_RESPONSES.pop(chat_id, None)


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


# ── Inline callbacks (spec §15.4 + FS2 §M8) ───────────────────────────────

# Whitelist of permitted callback_data prefixes. Unknown prefixes are logged
# + audited + silently dropped (NOT forwarded to agent — closes the
# arbitrary-callback-injection vector enumerated in M8).
ALLOWED_CALLBACK_PREFIXES: frozenset[str] = frozenset(
    {"stop:", "proposal:", "merge:", "confirm:", "cancel:"}
)


def _callback_prefix(data: str) -> str:
    """Return `<prefix>:` if data has a `:`, else the whole data string."""
    if ":" in data:
        return data.split(":", 1)[0] + ":"
    return data


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
    prefix = _callback_prefix(data)

    if prefix not in ALLOWED_CALLBACK_PREFIXES:
        log.warning(
            "callback_unknown_prefix_dropped",
            prefix=prefix,
            data=data,
            chat_id=chat_id,
        )
        record_telegram_event(
            direction="in",
            chat_id=chat_id,
            message_type="callback",
            original=data,
            extra={"action": "dropped", "reason": "unknown_prefix", "prefix": prefix},
        )
        return

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
    "attach_event_loop",
    "attach_state_db",
    "budget",
    "callback",
    "deliver_human_response",
    "forward_to_agent",
    "free_text",
    "help_cmd",
    "model",
    "reset_for_test",
    "start",
    "status",
    "stop",
]
