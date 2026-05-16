"""Per-worktree token bucket для Anthropic API ratelimit (spec §3 #7, handoff §5.2).

Anthropic limits (на 2026-05): TPM (input tokens per minute) и RPM (requests per
minute), tier-зависимые. 3-5 parallel workers легко hit лимит — token bucket
flattens spikes. Cache hits в ITPM не считаются — учитываем только non-cached
input + output.

Контракт:
- `TokenBucket(capacity, refill_per_second)` — generic.
- `RateLimiter(...).acquire(key, tokens)` — async, ждёт пока хватит.
- `try_acquire(key, tokens)` — non-blocking, возвращает bool.

Использование (S3+):
    rl = RateLimiter(tpm=50000, rpm=50)   # tier 1 defaults
    await rl.acquire("worker-1", tokens=2000)   # ждёт если bucket пуст
    # ... send Anthropic request ...

Реализация — стандартный refill-on-demand token bucket (без фонового таска).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field


@dataclass(slots=True)
class TokenBucket:
    """Refill-on-demand token bucket."""

    capacity: float
    refill_per_second: float
    tokens: float = 0.0
    last_refill_ts: float = field(default_factory=time.monotonic)

    def __post_init__(self) -> None:
        if self.capacity <= 0:
            raise ValueError("capacity must be > 0")
        if self.refill_per_second <= 0:
            raise ValueError("refill_per_second must be > 0")
        # Старт с полным bucket'ом — иначе первая операция всегда ждёт.
        if self.tokens == 0.0:
            self.tokens = self.capacity

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self.last_refill_ts
        if elapsed <= 0:
            return
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_second)
        self.last_refill_ts = now

    def try_consume(self, tokens: float) -> bool:
        """Non-blocking. Returns True если хватило, False иначе."""
        if tokens <= 0:
            return True
        self._refill()
        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False

    def wait_seconds(self, tokens: float) -> float:
        """Сколько секунд ждать до момента когда `tokens` будут доступны."""
        if tokens <= 0:
            return 0.0
        self._refill()
        deficit = tokens - self.tokens
        if deficit <= 0:
            return 0.0
        return deficit / self.refill_per_second


class RateLimiter:
    """Per-key Anthropic-style ratelimit (TPM + RPM).

    Каждый key (например worktree path или "global") получает свою пару bucket'ов:
    один по токенам (input non-cached + output), один по запросам.
    """

    def __init__(
        self,
        tpm: int = 50_000,
        rpm: int = 50,
        default_request_cost_tokens: int = 0,
    ) -> None:
        if tpm <= 0 or rpm <= 0:
            raise ValueError("tpm and rpm must be positive")
        self.tpm = tpm
        self.rpm = rpm
        self._default_request_cost_tokens = default_request_cost_tokens
        self._buckets_tokens: dict[str, TokenBucket] = {}
        self._buckets_requests: dict[str, TokenBucket] = {}
        self._lock = asyncio.Lock()

    def _bucket_tokens(self, key: str) -> TokenBucket:
        bucket = self._buckets_tokens.get(key)
        if bucket is None:
            bucket = TokenBucket(
                capacity=float(self.tpm),
                refill_per_second=float(self.tpm) / 60.0,
            )
            self._buckets_tokens[key] = bucket
        return bucket

    def _bucket_requests(self, key: str) -> TokenBucket:
        bucket = self._buckets_requests.get(key)
        if bucket is None:
            bucket = TokenBucket(
                capacity=float(self.rpm),
                refill_per_second=float(self.rpm) / 60.0,
            )
            self._buckets_requests[key] = bucket
        return bucket

    def try_acquire(self, key: str, tokens: int = 0) -> bool:
        """Non-blocking acquire — берёт 1 request + `tokens` токенов."""
        tb = self._bucket_tokens(key)
        rb = self._bucket_requests(key)
        if not rb.try_consume(1):
            return False
        if not tb.try_consume(float(tokens)):
            # Rollback request count — proxy: refund.
            rb.tokens = min(rb.capacity, rb.tokens + 1.0)
            return False
        return True

    async def acquire(self, key: str, tokens: int = 0) -> None:
        """Async acquire — ждёт пока хватит и токенов, и request slot'а."""
        while True:
            async with self._lock:
                if self.try_acquire(key, tokens):
                    return
                wait_tokens = self._bucket_tokens(key).wait_seconds(float(tokens))
                wait_requests = self._bucket_requests(key).wait_seconds(1.0)
                delay = max(wait_tokens, wait_requests, 0.01)
            await asyncio.sleep(delay)

    def snapshot(self, key: str) -> dict[str, float]:
        """Debug helper — текущее состояние bucket'а по ключу."""
        tb = self._bucket_tokens(key)
        rb = self._bucket_requests(key)
        tb._refill()
        rb._refill()
        return {
            "tokens_available": tb.tokens,
            "tokens_capacity": tb.capacity,
            "requests_available": rb.tokens,
            "requests_capacity": rb.capacity,
        }


__all__ = ["RateLimiter", "TokenBucket"]
