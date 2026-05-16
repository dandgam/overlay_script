"""Shared secret-redaction patterns (spec round 2 / C6).

Single source of truth for the API-key / token regex set used by:
- ``agent/safety/audit.py`` — append-only JSONL log scrubber (FS1 B6)
- ``bot/pii_detector.py::scrub_output`` — Telegram outbound first pass (C6)
- ``bot/handlers.py::_send_safe`` — defence-in-depth before reply_text

Each entry is a ``(compiled_pattern, kind)`` tuple. ``kind`` is the canonical
label embedded in the redaction placeholder, so audit-log readers and the
adversarial-corpus tests can assert WHICH pattern matched.

The list mirrors what was previously inlined in ``audit.py::_SECRET_PATTERNS``
plus the Telegram bot token regex (``\\d{8,10}:[A-Za-z0-9_-]{35}``) so that
real bot tokens accidentally echoed by the agent are redacted before egress.
"""

from __future__ import annotations

import re
from typing import Final

SECRET_PATTERNS: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    (re.compile(r"sk-ant-[A-Za-z0-9_-]{40,}"), "ANTHROPIC_KEY"),
    (re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"), "TELEGRAM_TOKEN"),
    (re.compile(r"\bghp_[A-Za-z0-9]{36}\b"), "GITHUB_PAT"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{82}\b"), "GITHUB_PAT"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS_KEY"),
    (re.compile(r"Bearer\s+[A-Za-z0-9_.\-+/=]{20,}", re.IGNORECASE), "BEARER"),
    (re.compile(r"https?://[^\s/:@]+:[^\s/@]+@[^\s/]+"), "URL_CREDS"),
)


def scrub_secrets(text: str) -> tuple[str, list[str]]:
    """Replace every secret match with ``[REDACTED:<kind>]``.

    Returns ``(scrubbed_text, kinds_found)``. ``kinds_found`` is order-preserved
    and de-duplicated — callers can log/audit which categories tripped without
    learning the raw value.
    """
    kinds: list[str] = []
    for pattern, kind in SECRET_PATTERNS:
        if pattern.search(text):
            text = pattern.sub(f"[REDACTED:{kind}]", text)
            if kind not in kinds:
                kinds.append(kind)
    return text, kinds


def scrub_secrets_generic(text: str, placeholder: str = "[REDACTED:SECRET]") -> str:
    """Single-placeholder variant for audit-log compatibility (FS1 contract).

    The append-only audit JSONL pre-dates kind-specific labels and asserts
    ``"[REDACTED:SECRET]"`` literal. Bot outbound uses ``scrub_secrets`` to
    preserve diagnostic value (which category leaked).
    """
    for pattern, _kind in SECRET_PATTERNS:
        text = pattern.sub(placeholder, text)
    return text


__all__ = ["SECRET_PATTERNS", "scrub_secrets", "scrub_secrets_generic"]
