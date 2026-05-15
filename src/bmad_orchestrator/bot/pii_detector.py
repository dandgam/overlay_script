"""PII detector wrapper (spec §15.5).

Stack: presidio-analyzer + spaCy ru_core_news_lg + custom RU patterns (паспорт, СНИЛС, ИНН).

Two entry points:
- scrub_input(text)  — на сообщение от пользователя ДО agent
- scrub_output(text) — на ответ агента ДО отправки в Telegram
"""

from __future__ import annotations

import re

# Russian-specific patterns (initial heuristic until Presidio integration finalized)
RU_PASSPORT = re.compile(r"\b\d{4}\s?\d{6}\b")
RU_SNILS = re.compile(r"\b\d{3}-\d{3}-\d{3}\s?\d{2}\b")
RU_INN = re.compile(r"\b\d{10}\b|\b\d{12}\b")
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE_RU = re.compile(r"\+?7[\s\-\(\)]?\d{3}[\s\-\(\)]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}")


def scrub_input(text: str) -> tuple[str, bool]:
    """Return (scrubbed_text, pii_found_flag).

    На input — не редактируем, просто детектим, пользователь решит.
    """
    found = bool(
        EMAIL.search(text)
        or PHONE_RU.search(text)
        or RU_PASSPORT.search(text)
        or RU_SNILS.search(text)
        or RU_INN.search(text)
    )
    return text, found


def scrub_output(text: str) -> tuple[str, list[str]]:
    """Return (redacted_text, list_of_redacted_categories).

    На output — обязательная redaction перед отправкой в Telegram.
    """
    redacted: list[str] = []
    if EMAIL.search(text):
        text = EMAIL.sub("[EMAIL]", text)
        redacted.append("EMAIL")
    if PHONE_RU.search(text):
        text = PHONE_RU.sub("[PHONE]", text)
        redacted.append("PHONE")
    if RU_PASSPORT.search(text):
        text = RU_PASSPORT.sub("[PASSPORT]", text)
        redacted.append("PASSPORT")
    if RU_SNILS.search(text):
        text = RU_SNILS.sub("[SNILS]", text)
        redacted.append("SNILS")
    if RU_INN.search(text):
        text = RU_INN.sub("[INN]", text)
        redacted.append("INN")
    # TODO: presidio для имён, адресов, more sophisticated detection
    return text, redacted
