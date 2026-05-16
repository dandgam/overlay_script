"""PII detector (spec §15.5).

Стек: regex для RU-specific (паспорт, СНИЛС, ИНН, email, phone) + опциональный
Presidio (имена, локации, организации) при наличии в окружении. Safelist для
технических identifiers (PIDs, commit hashes, file paths, env-style токены)
снижает false-positive на input от пользователя-разработчика.

Two entry points:
- scrub_input(text)  — детекция перед отправкой в agent (НЕ редактируем).
- scrub_output(text) — обязательная redaction ответа агента перед Telegram.

Presidio загружается лениво и опционально. Если presidio_analyzer или модель
spaCy ru_core_news_lg отсутствует, fallback на regex — никаких import-time
exceptions; missing-presidio = graceful degradation, не падение.
"""

from __future__ import annotations

import re
from typing import Any

# ── Regex patterns (always-on baseline) ─────────────────────────────────────────

EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
# H13 fix: extended lookbehind to include `:`, `;`, `,`, `/` (so that prefixes
# like `tel:`, `phone;`, csv `,`, URL path `/` don't suppress detection) +
# alternative `8\d{10}` for the legacy 8-prefix Russian phone format.
PHONE_RU = re.compile(
    r"(?:(?<=\s)|(?<=^)|(?<=[(\[:;,/]))"
    r"(?:\+?7[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}|8\d{10})\b"
)
RU_PASSPORT = re.compile(r"\b\d{4}\s\d{6}\b")
RU_SNILS = re.compile(r"\b\d{3}-\d{3}-\d{3}\s?\d{2}\b")
RU_INN_10 = re.compile(r"(?<![\d-])\d{10}(?![\d-])")
RU_INN_12 = re.compile(r"(?<![\d-])\d{12}(?![\d-])")

# Technical identifiers that look like PII but aren't.
# GIT_SHA: must contain ≥1 hex letter — иначе pure-digit строка может быть INN/account.
GIT_SHA = re.compile(r"\b(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b")
UUID_LIKE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
# H13 fix: lookahead-at-start `(?!\S*@)` rejects the entire token if it
# contains an `@` before the next whitespace — `/var/lib/user@host.com` is
# treated as an email-bearing token, not a pure path, so EMAIL detection on
# the inner span isn't suppressed by the safelist mask. (Spec §15.5 wording
# `[^/]*@` doesn't work because the regex would backtrack to a shorter path
# match and the post-match lookahead would then succeed.)
PATH_LIKE = re.compile(r"(?:^|\s)(?!\S*@)(?:/[\w.\-]+){2,}")
PID_LIKE = re.compile(r"\bpid[=:\s]+\d+\b", re.IGNORECASE)

_INN_10_WEIGHTS = (2, 4, 10, 3, 5, 9, 4, 6, 8, 0)
_INN_12_WEIGHTS_1 = (7, 2, 4, 10, 3, 5, 9, 4, 6, 8, 0, 0)
_INN_12_WEIGHTS_2 = (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8, 0)


def _inn_10_valid(digits: str) -> bool:
    if len(digits) != 10 or not digits.isdigit():
        return False
    s = sum(int(d) * w for d, w in zip(digits, _INN_10_WEIGHTS, strict=False))
    return (s % 11) % 10 == int(digits[9])


def _inn_12_valid(digits: str) -> bool:
    if len(digits) != 12 or not digits.isdigit():
        return False
    s1 = sum(int(d) * w for d, w in zip(digits, _INN_12_WEIGHTS_1, strict=False))
    s2 = sum(int(d) * w for d, w in zip(digits, _INN_12_WEIGHTS_2, strict=False))
    return (s1 % 11) % 10 == int(digits[10]) and (s2 % 11) % 10 == int(digits[11])


# ── Safelist (anti-false-positive) ──────────────────────────────────────────────


def _build_safelist_mask(text: str) -> list[tuple[int, int]]:
    """Return list of (start, end) ranges to ignore (technical identifiers)."""
    masks: list[tuple[int, int]] = []
    for pat in (GIT_SHA, UUID_LIKE, PATH_LIKE, PID_LIKE):
        for m in pat.finditer(text):
            masks.append((m.start(), m.end()))
    return masks


def _in_mask(span: tuple[int, int], masks: list[tuple[int, int]]) -> bool:
    s, e = span
    return any(ms <= s and e <= me for ms, me in masks)


# ── Presidio (optional, lazy) ───────────────────────────────────────────────────

_PRESIDIO_ANALYZER: Any = None
_PRESIDIO_FAILED: bool = False


def _get_presidio() -> Any | None:
    """Lazy-construct Presidio AnalyzerEngine. Returns None if not available."""
    global _PRESIDIO_ANALYZER, _PRESIDIO_FAILED
    if _PRESIDIO_FAILED:
        return None
    if _PRESIDIO_ANALYZER is not None:
        return _PRESIDIO_ANALYZER
    try:
        from presidio_analyzer import AnalyzerEngine
        from presidio_analyzer.nlp_engine import NlpEngineProvider

        provider = NlpEngineProvider(
            nlp_configuration={
                "nlp_engine_name": "spacy",
                "models": [{"lang_code": "ru", "model_name": "ru_core_news_lg"}],
            }
        )
        nlp_engine = provider.create_engine()
        _PRESIDIO_ANALYZER = AnalyzerEngine(nlp_engine=nlp_engine, supported_languages=["ru"])
        return _PRESIDIO_ANALYZER
    except Exception:
        _PRESIDIO_FAILED = True
        return None


_PRESIDIO_ENTITY_MAP: dict[str, str] = {
    "PERSON": "PERSON",
    "LOCATION": "LOCATION",
    "ORGANIZATION": "ORG",
    "EMAIL_ADDRESS": "EMAIL",
    "PHONE_NUMBER": "PHONE",
}


def _presidio_findings(text: str) -> list[tuple[int, int, str]]:
    """Return list of (start, end, label) from Presidio, or [] if unavailable."""
    analyzer = _get_presidio()
    if analyzer is None:
        return []
    try:
        results = analyzer.analyze(text=text, language="ru")
    except Exception:
        return []
    out: list[tuple[int, int, str]] = []
    for r in results:
        label = _PRESIDIO_ENTITY_MAP.get(r.entity_type)
        if label:
            out.append((r.start, r.end, label))
    return out


# ── Public API ─────────────────────────────────────────────────────────────────


def _regex_categories(text: str, masks: list[tuple[int, int]]) -> list[str]:
    """Return list of category labels found by regex (filtered by safelist)."""
    found: list[str] = []
    for label, pat in (
        ("EMAIL", EMAIL),
        ("PHONE", PHONE_RU),
        ("PASSPORT", RU_PASSPORT),
        ("SNILS", RU_SNILS),
    ):
        for m in pat.finditer(text):
            if not _in_mask((m.start(), m.end()), masks):
                found.append(label)
                break
    for m in RU_INN_10.finditer(text):
        if _in_mask((m.start(), m.end()), masks):
            continue
        if _inn_10_valid(m.group()):
            found.append("INN")
            break
    for m in RU_INN_12.finditer(text):
        if _in_mask((m.start(), m.end()), masks):
            continue
        if _inn_12_valid(m.group()):
            found.append("INN")
            break
    return found


def scrub_input(text: str) -> tuple[str, list[str]]:
    """Detect PII in user input. NEVER modifies — user decides via inline button.

    Returns (text_unchanged, list_of_categories_detected).
    """
    masks = _build_safelist_mask(text)
    categories = _regex_categories(text, masks)
    for start, end, label in _presidio_findings(text):
        if _in_mask((start, end), masks):
            continue
        if label not in categories:
            categories.append(label)
    return text, categories


def scrub_output(text: str) -> tuple[str, list[str]]:
    """Redact PII in agent output before sending to Telegram.

    Returns (redacted_text, list_of_redacted_categories).
    """
    masks = _build_safelist_mask(text)
    redacted: list[str] = []

    def _sub_with_safelist(pattern: re.Pattern[str], placeholder: str, label: str) -> None:
        nonlocal text
        new_parts: list[str] = []
        last = 0
        changed = False
        for m in pattern.finditer(text):
            if _in_mask((m.start(), m.end()), masks):
                continue
            if label == "INN":
                digits = m.group()
                valid = (
                    _inn_10_valid(digits) if len(digits) == 10 else _inn_12_valid(digits)
                )
                if not valid:
                    continue
            new_parts.append(text[last : m.start()])
            new_parts.append(placeholder)
            last = m.end()
            changed = True
        if changed:
            new_parts.append(text[last:])
            text = "".join(new_parts)
            if label not in redacted:
                redacted.append(label)

    _sub_with_safelist(EMAIL, "[EMAIL]", "EMAIL")
    _sub_with_safelist(PHONE_RU, "[PHONE]", "PHONE")
    _sub_with_safelist(RU_PASSPORT, "[PASSPORT]", "PASSPORT")
    _sub_with_safelist(RU_SNILS, "[SNILS]", "SNILS")
    _sub_with_safelist(RU_INN_10, "[INN]", "INN")
    _sub_with_safelist(RU_INN_12, "[INN]", "INN")

    # Presidio second pass — после regex, masks пересчитывать не надо: redact
    # placeholders уже не вызовут false-positive в Presidio.
    presidio_hits = _presidio_findings(text)
    if presidio_hits:
        masks2 = _build_safelist_mask(text)
        spans = sorted(presidio_hits, key=lambda x: x[0], reverse=True)
        for start, end, label in spans:
            if _in_mask((start, end), masks2):
                continue
            text = text[:start] + f"[{label}]" + text[end:]
            if label not in redacted:
                redacted.append(label)

    return text, redacted


__all__ = [
    "scrub_input",
    "scrub_output",
]
