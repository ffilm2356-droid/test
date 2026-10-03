"""API key rotation pool — round-robin across multiple keys to bypass per-key quota.

Usage:
  pool = KeyPool(["key1", "key2", "key3"])
  key = await pool.get()           # round-robin
  await pool.report_error(key)     # auto-cooldown on 429
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger("mediaforge.key_pool")


@dataclass
class KeyStats:
    key: str
    requests: int = 0
    errors: int = 0
    rate_limited_until: float = 0.0
    last_used: float = 0.0


class KeyPool:
    """Round-robin API key pool with per-key cooldown on rate limits."""

    def __init__(self, keys: list[str]):
        if not keys:
            raise ValueError("KeyPool requires at least one key")
        self._stats = [KeyStats(key=k) for k in keys]
        self._cycle = itertools.cycle(range(len(self._stats)))
        self._lock = asyncio.Lock()

    @property
    def size(self) -> int:
        return len(self._stats)

    async def get(self) -> str:
        async with self._lock:
            now = time.monotonic()
            for _ in range(len(self._stats) * 2):
                idx = next(self._cycle)
                ks = self._stats[idx]
                if ks.rate_limited_until > now:
                    continue
                ks.requests += 1
                ks.last_used = now
                return ks.key

            best = min(self._stats, key=lambda k: k.rate_limited_until)
            wait = best.rate_limited_until - now
            if wait > 0:
                log.info("all keys rate-limited, waiting %.0fs", wait)
                await asyncio.sleep(wait)
            best.requests += 1
            best.last_used = time.monotonic()
            return best.key

    async def report_error(self, key: str, cooldown: float = 60.0):
        for ks in self._stats:
            if ks.key == key:
                ks.errors += 1
                ks.rate_limited_until = time.monotonic() + cooldown
                log.debug("key ...%s cooled down %.0fs (errors=%d)",
                          key[-6:], cooldown, ks.errors)
                break

    async def report_success(self, key: str):
        for ks in self._stats:
            if ks.key == key:
                ks.errors = max(0, ks.errors - 1)
                break

    def stats(self) -> list[dict]:
        now = time.monotonic()
        return [
            {
                "key": f"...{ks.key[-6:]}",
                "requests": ks.requests,
                "errors": ks.errors,
                "cooldown_remaining": max(0, ks.rate_limited_until - now),
            }
            for ks in self._stats
        ]
