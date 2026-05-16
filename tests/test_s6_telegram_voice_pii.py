"""S6 acceptance tests — Telegram bot + voice + PII (spec §15).

Coverage:
- PII detector on 20+ RU/EN sample phrases (input detection + output redaction).
- Safelist для technical IDs (git SHAs, UUIDs, paths, PIDs).
- INN checksum validation (rejects 10/12-digit junk).
- voice provider factory — все 5 STT, disabled, invalid name, missing creds.
- transcribe_with_fallback — happy, primary-fails-fallback-succeeds, both-fail.
- post_process_transcription — technical-term replacement.
- bot/audit.py — telegram.jsonl writes + BMAD_TELEGRAM_AUDIT_LOG override.
- handlers — whitelist check (deny path, allow path), inline keyboard на /stop,
    free_text → forward_to_agent, callback dispatch.
- telegram_bot.build_application — refuses без token / без whitelist.
- bot.main._check_config — pass/fail paths.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from bmad_orchestrator.bot import handlers, pii_detector, voice_handler
from bmad_orchestrator.bot.audit import record_telegram_event, telegram_audit_path
from bmad_orchestrator.bot.main import _check_config
from bmad_orchestrator.bot.pii_detector import scrub_input, scrub_output
from bmad_orchestrator.bot.voice_handler import post_process_transcription
from bmad_orchestrator.bot.voice_providers import (
    VALID_STT_NAMES,
    STTProvider,
    WhisperLocalSTT,
    make_stt_provider,
    transcribe_with_fallback,
)

# ── fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_audit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BMAD_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("BMAD_TELEGRAM_AUDIT_LOG", str(tmp_path / "telegram.jsonl"))
    monkeypatch.setenv("ORCHESTRATOR_TARGET_PROJECT", str(tmp_path / "target"))


def _patched_settings(
    chat_ids: list[int] | None = None,
    bot_token: str | None = None,
) -> Any:
    """Build a Settings-shaped object via pydantic — Settings doesn't support
    nested env vars, so tests inject via monkeypatched load_settings."""
    from bmad_orchestrator.config import Settings, TelegramConfig

    s = Settings()
    s.telegram = TelegramConfig(
        bot_token=bot_token,
        chat_id_whitelist=chat_ids or [],
    )
    return s


def _install_settings(monkeypatch: pytest.MonkeyPatch, **kw: Any) -> None:
    s = _patched_settings(**kw)
    monkeypatch.setattr("bmad_orchestrator.bot.handlers.load_settings", lambda: s)
    monkeypatch.setattr("bmad_orchestrator.bot.telegram_bot.load_settings", lambda: s)
    monkeypatch.setattr("bmad_orchestrator.bot.voice_handler.load_settings", lambda: s)
    monkeypatch.setattr("bmad_orchestrator.bot.main.load_settings", lambda: s)


@pytest.fixture(autouse=True)
def _reset_provider_cache() -> None:
    voice_handler.reset_provider_cache()
    yield
    voice_handler.reset_provider_cache()


@pytest.fixture(autouse=True)
def _no_presidio(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable Presidio in tests — spaCy ru_core_news_lg может отсутствовать.

    PII tests должны проходить на чистом regex baseline. Если Presidio доступен —
    отдельные tests могут включить его явно.
    """
    monkeypatch.setattr(pii_detector, "_PRESIDIO_FAILED", True)
    monkeypatch.setattr(pii_detector, "_PRESIDIO_ANALYZER", None)


# ── PII regex coverage (20+ RU sample phrases) ─────────────────────────────────


@pytest.mark.parametrize(
    "phrase,expected",
    [
        # POSITIVE — должны детектиться
        ("свяжись с ivan@example.com", ["EMAIL"]),
        ("мой имейл petr@yandex.ru, отвечай", ["EMAIL"]),
        ("звони на +7 (495) 123-45-67 сегодня", ["PHONE"]),
        ("телефон +79991234567 запиши", ["PHONE"]),
        ("СНИЛС 123-456-789 01 для оформления", ["SNILS"]),
        ("паспорт 4509 123456 серия номер", ["PASSPORT"]),
        ("ИНН 7707083893 у Сбербанка", ["INN"]),  # valid Sberbank INN
        # email + phone в одной фразе
        ("пиши на a@b.ru или +7-905-555-44-33", ["EMAIL", "PHONE"]),
        # NEGATIVE — должны игнориться (safelist + invalid checksum)
        ("worker pid=12345 hung", []),
        ("commit 7c18728 содержит fix", []),
        ("path /home/server/bmad/state.db missing", []),
        ("ИНН 1234567890 неправильный", []),  # invalid checksum
        ("ИНН 123456789012 левый", []),  # invalid checksum
        ("uuid 550e8400-e29b-41d4-a716-446655440000 в логе", []),
        ("свободный текст про планирование", []),
        ("запусти одиссей 1a, два воркера", []),
        ("дорого, на сонет переключи", []),
        # numeric но не PII
        ("budget $50.0 alarm", []),
        ("17 задач в очереди", []),
        ("номер задачи 1.10a статус", []),
        ("session_42 retry_count=2", []),
    ],
)
def test_scrub_input_sample_phrases(phrase: str, expected: list[str]) -> None:
    _, categories = scrub_input(phrase)
    assert sorted(set(categories)) == sorted(set(expected)), (
        f"phrase={phrase!r}: got {categories}, expected {expected}"
    )


def test_scrub_input_returns_text_unchanged() -> None:
    raw = "email ivan@example.com, телефон +79991234567"
    text, found = scrub_input(raw)
    assert text == raw  # input — НЕ редактируем
    assert "EMAIL" in found
    assert "PHONE" in found


# ── PII output redaction ───────────────────────────────────────────────────────


def test_scrub_output_redacts_email() -> None:
    text, cats = scrub_output("связался с ivan@example.com")
    assert "[EMAIL]" in text
    assert "ivan@example.com" not in text
    assert cats == ["EMAIL"]


def test_scrub_output_redacts_phone() -> None:
    text, cats = scrub_output("звонил по +7 (495) 123-45-67")
    assert "[PHONE]" in text
    assert "495" not in text
    assert "PHONE" in cats


def test_scrub_output_redacts_multiple_categories() -> None:
    text, cats = scrub_output(
        "email ivan@x.ru, тел +79991234567, СНИЛС 123-456-789 01, ИНН 7707083893"
    )
    assert "[EMAIL]" in text
    assert "[PHONE]" in text
    assert "[SNILS]" in text
    assert "[INN]" in text
    assert set(cats) >= {"EMAIL", "PHONE", "SNILS", "INN"}


def test_scrub_output_preserves_technical_ids() -> None:
    # Path и UUID не должны редактиться как PII
    raw = "merged 7c18728 at /home/server/state.db, session 550e8400-e29b-41d4-a716-446655440000"
    text, cats = scrub_output(raw)
    assert "7c18728" in text
    assert "/home/server/state.db" in text
    assert "550e8400-e29b-41d4-a716-446655440000" in text
    assert cats == []


def test_scrub_output_idempotent() -> None:
    raw = "[EMAIL] и [PHONE] уже redacted"
    text, cats = scrub_output(raw)
    assert text == raw
    assert cats == []


def test_scrub_output_inn_rejects_invalid_checksum() -> None:
    text, cats = scrub_output("ИНН 1234567890 неправильный")
    assert "1234567890" in text  # not redacted
    assert "INN" not in cats


# ── voice provider factory ─────────────────────────────────────────────────────


def test_factory_disabled_returns_none() -> None:
    assert make_stt_provider("disabled") is None


def test_factory_whisper_local() -> None:
    p = make_stt_provider("whisper_local")
    assert isinstance(p, WhisperLocalSTT)
    assert p.name == "whisper_local"
    assert p.estimate_cost_usd(60) == 0.0


def test_factory_whisper_api_requires_key() -> None:
    with pytest.raises(ValueError, match="whisper_api requires api_key"):
        make_stt_provider("whisper_api")
    p = make_stt_provider("whisper_api", api_key="sk-x")
    assert p is not None
    assert p.name == "whisper_api"
    # $0.006/min
    assert p.estimate_cost_usd(60) == pytest.approx(0.006)


def test_factory_yandex_requires_folder() -> None:
    with pytest.raises(ValueError, match="folder_id"):
        make_stt_provider("yandex_speechkit", api_key="k")
    p = make_stt_provider("yandex_speechkit", api_key="k", folder_id="f")
    assert p is not None
    assert p.estimate_cost_usd(60) == pytest.approx(0.015)


def test_factory_google_requires_credentials() -> None:
    with pytest.raises(ValueError, match="credentials_path"):
        make_stt_provider("google_stt_v2")


def test_factory_claude_audio() -> None:
    p = make_stt_provider("claude_audio", api_key="sk-ant")
    assert p is not None
    assert p.name == "claude_audio"


def test_factory_invalid_provider_raises() -> None:
    with pytest.raises(ValueError, match="Unknown STT provider"):
        make_stt_provider("not-a-real-provider")


def test_all_5_concrete_providers_listed() -> None:
    # Spec acceptance §15.8.1 — 5 + disabled
    assert {
        "whisper_local",
        "whisper_api",
        "claude_audio",
        "yandex_speechkit",
        "google_stt_v2",
        "disabled",
    } == set(VALID_STT_NAMES)


# ── fallback chain ─────────────────────────────────────────────────────────────


class _MockSTT(STTProvider):
    def __init__(self, name: str, return_text: str = "ok", raise_exc: bool = False) -> None:
        self.name = name
        self._return = return_text
        self._raise = raise_exc
        self.calls = 0

    async def transcribe(self, audio_path: Path, language: str = "ru") -> str:
        self.calls += 1
        if self._raise:
            raise RuntimeError(f"{self.name} simulated failure")
        return self._return

    def estimate_cost_usd(self, duration_seconds: float) -> float:
        return 0.0


@pytest.mark.asyncio
async def test_fallback_primary_succeeds() -> None:
    primary = _MockSTT("yandex", return_text="привет")
    fallback = _MockSTT("whisper")
    text, used = await transcribe_with_fallback(primary, fallback, Path("/tmp/x.ogg"))
    assert text == "привет"
    assert used == "yandex"
    assert primary.calls == 1
    assert fallback.calls == 0


@pytest.mark.asyncio
async def test_fallback_used_when_primary_fails() -> None:
    primary = _MockSTT("yandex", raise_exc=True)
    fallback = _MockSTT("whisper", return_text="привет от fallback")
    text, used = await transcribe_with_fallback(primary, fallback, Path("/tmp/x.ogg"))
    assert text == "привет от fallback"
    assert used == "whisper"
    assert primary.calls == 1
    assert fallback.calls == 1


@pytest.mark.asyncio
async def test_fallback_both_fail_raises_primary() -> None:
    primary = _MockSTT("yandex", raise_exc=True)
    fallback = _MockSTT("whisper", raise_exc=True)
    with pytest.raises(RuntimeError, match="yandex simulated failure"):
        await transcribe_with_fallback(primary, fallback, Path("/tmp/x.ogg"))


@pytest.mark.asyncio
async def test_fallback_no_fallback_raises_primary() -> None:
    primary = _MockSTT("yandex", raise_exc=True)
    with pytest.raises(RuntimeError, match="yandex simulated failure"):
        await transcribe_with_fallback(primary, None, Path("/tmp/x.ogg"))


# ── post-processing typos ──────────────────────────────────────────────────────


def test_post_process_replaces_technical_terms() -> None:
    assert "Cargo.toml" in post_process_transcription("проверь карго тошнол")
    assert "worker" in post_process_transcription("один вокер упал")
    assert "PR" in post_process_transcription("открой пиар")
    assert "sonnet" in post_process_transcription("переключи на сонет")


def test_post_process_no_match_passthrough() -> None:
    assert post_process_transcription("обычный русский текст") == "обычный русский текст"


# ── audit log ──────────────────────────────────────────────────────────────────


def test_audit_path_uses_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "custom-tg.jsonl"
    monkeypatch.setenv("BMAD_TELEGRAM_AUDIT_LOG", str(target))
    assert telegram_audit_path() == target


def test_record_telegram_event_writes_line() -> None:
    entry = record_telegram_event(
        direction="in",
        chat_id=42,
        message_type="text",
        original="привет",
        redacted="привет",
        pii_categories=[],
    )
    assert entry["direction"] == "in"
    assert entry["chat_id"] == 42
    path = telegram_audit_path()
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["message_type"] == "text"
    assert parsed["original"] == "привет"


def test_record_telegram_event_appends() -> None:
    record_telegram_event(direction="in", chat_id=1, message_type="text", original="a")
    record_telegram_event(direction="out", chat_id=1, message_type="text", redacted="b")
    lines = telegram_audit_path().read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2


# ── handlers (whitelist + commands + free_text + callback) ────────────────────


def _mk_update(chat_id: int | None, *, text: str = "", voice: Any = None) -> MagicMock:
    update = MagicMock()
    if chat_id is None:
        update.effective_chat = None
    else:
        update.effective_chat.id = chat_id
    update.message = MagicMock()
    update.message.text = text
    update.message.voice = voice
    update.message.reply_text = AsyncMock()
    update.callback_query = None
    return update


@pytest.mark.asyncio
async def test_whitelist_denies_unknown_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_settings(monkeypatch, chat_ids=[111])
    update = _mk_update(999, text="hi")
    await handlers.free_text(update, MagicMock())
    update.message.reply_text.assert_not_called()


@pytest.mark.asyncio
async def test_whitelist_allows_listed_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_settings(monkeypatch, chat_ids=[111])
    update = _mk_update(111, text="запусти одиссей")
    await handlers.free_text(update, MagicMock())
    update.message.reply_text.assert_called_once()
    args, _ = update.message.reply_text.call_args
    # Output PII-scrubbed; agent stub mentions "оркестратор не подключён"
    assert "оркестратор" in args[0] or "S8" in args[0]


@pytest.mark.asyncio
async def test_stop_returns_inline_keyboard(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_settings(monkeypatch, chat_ids=[111])
    update = _mk_update(111)
    await handlers.stop(update, MagicMock())
    update.message.reply_text.assert_called_once()
    _, kwargs = update.message.reply_text.call_args
    keyboard = kwargs.get("reply_markup")
    assert keyboard is not None
    # InlineKeyboardMarkup — две строки: [graceful, hard], [cancel]
    buttons = keyboard.inline_keyboard
    assert len(buttons) == 2
    callbacks = {b.callback_data for row in buttons for b in row}
    assert callbacks == {"stop:graceful", "stop:hard", "stop:cancel"}


@pytest.mark.asyncio
async def test_callback_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_settings(monkeypatch, chat_ids=[111])
    update = MagicMock()
    update.effective_chat.id = 111
    update.message = None
    update.callback_query = MagicMock()
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    update.callback_query.data = "stop:graceful"

    await handlers.callback(update, MagicMock())
    update.callback_query.answer.assert_awaited_once()
    update.callback_query.edit_message_text.assert_awaited_once()
    args, _ = update.callback_query.edit_message_text.call_args
    assert "мягко" in args[0]


@pytest.mark.asyncio
async def test_free_text_pii_audited(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_settings(monkeypatch, chat_ids=[111])
    update = _mk_update(111, text="email ivan@example.com")
    await handlers.free_text(update, MagicMock())
    lines = telegram_audit_path().read_text(encoding="utf-8").splitlines()
    parsed = [json.loads(line) for line in lines]
    inbound = [p for p in parsed if p["direction"] == "in"]
    assert any(p.get("pii") and "EMAIL" in p["pii"] for p in inbound)


# ── telegram_bot.build_application ─────────────────────────────────────────────


def test_build_application_requires_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from bmad_orchestrator.bot.telegram_bot import build_application

    _install_settings(monkeypatch, chat_ids=[111], bot_token=None)
    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN"):
        build_application()


def test_build_application_requires_whitelist(monkeypatch: pytest.MonkeyPatch) -> None:
    from bmad_orchestrator.bot.telegram_bot import build_application

    _install_settings(monkeypatch, chat_ids=[], bot_token="x")
    with pytest.raises(RuntimeError, match="whitelist"):
        build_application()


def test_build_application_succeeds_with_full_config(monkeypatch: pytest.MonkeyPatch) -> None:
    from bmad_orchestrator.bot.telegram_bot import build_application

    _install_settings(monkeypatch, chat_ids=[111], bot_token="12345:fake")
    app = build_application()
    assert app is not None
    # Smoke: voice + free_text + slash + callback handlers all registered.
    # PTB stores them in app.handlers dict keyed by group (default 0).
    group = app.handlers[0]
    assert len(group) >= 8


# ── bot.main._check_config ─────────────────────────────────────────────────────


def test_check_config_fails_without_token(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_settings(monkeypatch, chat_ids=[111], bot_token=None)
    code = _check_config()
    assert code == 1
    err = capsys.readouterr().err
    assert "TELEGRAM_BOT_TOKEN" in err


def test_check_config_succeeds(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_settings(monkeypatch, chat_ids=[111], bot_token="x")
    code = _check_config()
    assert code == 0
    out = capsys.readouterr().out
    assert "bot_token" in out
    assert "chat_id_whitelist" in out
