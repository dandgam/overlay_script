"""asyncio.Semaphore-based subprocess pool for `claude -p` workers (spec §2).

Caps parallelism at max_parallel (MVP=2, scale to 3-5 after validation).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import Any


class SubprocessPool:
    def __init__(self, max_parallel: int = 3):
        self.semaphore = asyncio.Semaphore(max_parallel)

    async def run(self, fn: Awaitable[Any]) -> Any:
        async with self.semaphore:
            return await fn
