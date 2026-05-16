"""Token usage tracker (deterministic, spec §10 observability) + Anthropic
2026 pricing table.

Parses Anthropic `usage` blocks:
  usage.input_tokens
  usage.cache_creation_input_tokens   # 1h cache writes
  usage.cache_read_input_tokens       # cache hits
  usage.output_tokens

`usd_cost(model, usage)` returns Decimal (financial precision — never float).
NaN / inf / negative token counts → ValueError (safe-default halt at caller).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal, getcontext
from typing import Final

getcontext().prec = 28


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Token usage breakdown from Anthropic JSONL `usage` block."""

    input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    output_tokens: int = 0

    def __post_init__(self) -> None:
        for name in (
            "input_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
            "output_tokens",
        ):
            v = getattr(self, name)
            if not isinstance(v, int) or isinstance(v, bool):
                raise TypeError(f"{name} must be int, got {type(v).__name__}")
            if v < 0:
                raise ValueError(f"{name} must be non-negative, got {v}")

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.cache_creation_input_tokens
            + self.cache_read_input_tokens
            + self.output_tokens
        )


# Anthropic published 2026 pricing (USD per 1M tokens).
# Cache write 1h price = base input × 1.25; cache read = base input × 0.1.
# Source: https://www.anthropic.com/pricing (verified 2026-05-16).
MODEL_PRICING_USD_PER_MTOK: Final[dict[str, dict[str, Decimal]]] = {
    "claude-opus-4-7": {
        "input": Decimal("15"),
        "output": Decimal("75"),
        "cache_write_1h": Decimal("18.75"),
        "cache_read": Decimal("1.50"),
    },
    "claude-sonnet-4-6": {
        "input": Decimal("3"),
        "output": Decimal("15"),
        "cache_write_1h": Decimal("3.75"),
        "cache_read": Decimal("0.30"),
    },
    "claude-haiku-4-5": {
        "input": Decimal("1"),
        "output": Decimal("5"),
        "cache_write_1h": Decimal("1.25"),
        "cache_read": Decimal("0.10"),
    },
}

_PER_MTOK_DIVISOR: Final[Decimal] = Decimal("1000000")


def usd_cost(model: str, usage: TokenUsage) -> Decimal:
    """Exact USD cost as Decimal for the given model + usage.

    Raises ValueError if the model is unknown.
    """
    pricing = MODEL_PRICING_USD_PER_MTOK.get(model)
    if pricing is None:
        raise ValueError(
            f"unknown model {model!r}; expected one of "
            f"{sorted(MODEL_PRICING_USD_PER_MTOK)}"
        )
    cost = (
        Decimal(usage.input_tokens) * pricing["input"]
        + Decimal(usage.output_tokens) * pricing["output"]
        + Decimal(usage.cache_creation_input_tokens) * pricing["cache_write_1h"]
        + Decimal(usage.cache_read_input_tokens) * pricing["cache_read"]
    ) / _PER_MTOK_DIVISOR
    return cost


def is_finite_spend(spent_usd: float | Decimal) -> bool:
    """True iff spent_usd is finite and non-negative.

    NaN/inf/negative spend indicates corruption; caller MUST halt + audit critical.
    """
    if isinstance(spent_usd, Decimal):
        if spent_usd.is_nan() or spent_usd.is_infinite():
            return False
        return spent_usd >= 0
    if not isinstance(spent_usd, (int, float)):
        return False
    if isinstance(spent_usd, bool):
        return False
    if not math.isfinite(spent_usd):
        return False
    return spent_usd >= 0


__all__ = [
    "MODEL_PRICING_USD_PER_MTOK",
    "TokenUsage",
    "is_finite_spend",
    "usd_cost",
]
