"""Локализация per spec §14.3.

Простой dict-lookup (``locale/<lang>.yaml``), без gettext. Pluralization не нужна —
сообщения короткие и фиксированные.

Use:
    >>> from bmad_orchestrator.cli.i18n import t
    >>> t("worker.completed", pid=1234, story="1-1")

При отсутствии ключа возвращается сам ключ — fail-soft, чтобы CLI оставался
работоспособным даже если кто-то забыл добавить перевод.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_LOCALE_DIR = Path(__file__).resolve().parent.parent / "locale"
_DEFAULT_LOCALE = "ru"


@lru_cache(maxsize=4)
def _load(locale: str) -> dict[str, Any]:
    """Read locale/<locale>.yaml; fallback to default on missing file."""
    path = _LOCALE_DIR / f"{locale}.yaml"
    if not path.is_file():
        path = _LOCALE_DIR / f"{_DEFAULT_LOCALE}.yaml"
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        return {}
    return data


def _resolve(table: dict[str, Any], key: str) -> str | None:
    cur: Any = table
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur if isinstance(cur, str) else None


def t(key: str, *, locale: str | None = None, **kwargs: Any) -> str:
    """Translate a dotted key into the locale string.

    ``locale`` defaults to settings.locale (ru). Missing keys return the key
    itself so CLI continues to render — каждый missing key виден глазами.
    """
    lang = locale or _current_locale()
    table = _load(lang)
    raw = _resolve(table, key)
    if raw is None:
        # Try default locale as fallback before surrendering.
        if lang != _DEFAULT_LOCALE:
            raw = _resolve(_load(_DEFAULT_LOCALE), key)
    if raw is None:
        return key
    try:
        return raw.format(**kwargs)
    except (KeyError, IndexError):
        return raw


def _current_locale() -> str:
    """Lazy settings lookup — avoids module-import-time side effects."""
    try:
        from bmad_orchestrator.config import load_settings

        return load_settings().locale or _DEFAULT_LOCALE
    except Exception:
        return _DEFAULT_LOCALE


__all__ = ["t"]
