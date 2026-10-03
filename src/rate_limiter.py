"""Token-bucket rate limiter — per-provider, async-safe."""

from __future__ import annotations

import asyncio
import time

from .config import RateLimit


class TokenBucket:
    """Async token-bucket rate limiter."""

    def __init__(self, cfg: RateLimit):
        self.rate = cfg.calls_per_second
        self.burst = cfg.burst
        self._tokens = float(cfg.burst)
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self):
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last
            self._tokens = min(self.burst, self._tokens + elapsed * self.rate)
            self._last = now

            if self._tokens < 1.0:
                wait = (1.0 - self._tokens) / self.rate
                await asyncio.sleep(wait)
                self._tokens = 0.0
                self._last = time.monotonic()
            else:
                self._tokens -= 1.0
