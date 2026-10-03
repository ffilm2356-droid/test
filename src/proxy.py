"""Proxy rotation + VPN gateway — SOCKS5, HTTP, HTTPS proxy support.

Turns any local VPN connection into a proxy endpoint so you don't
need to buy external proxies for SSL traffic.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import aiohttp
from aiohttp_socks import ProxyConnector, ProxyType

from .config import ProxyConfig

log = logging.getLogger("mediaforge.proxy")


@dataclass
class ProxyStats:
    url: str
    successes: int = 0
    failures: int = 0
    last_used: float = 0.0
    avg_latency: float = 0.0
    banned_until: float = 0.0


class ProxyPool:
    """Round-robin proxy pool with health tracking and VPN gateway."""

    def __init__(self, cfg: ProxyConfig):
        self.cfg = cfg
        self._proxies: list[ProxyStats] = []
        self._cycle = itertools.cycle([])
        self._lock = asyncio.Lock()
        self._call_count = 0

        if cfg.proxies:
            self._proxies = [ProxyStats(url=p) for p in cfg.proxies]
            self._cycle = itertools.cycle(self._proxies)

        if cfg.vpn_gateway:
            vpn_proxy = self._build_vpn_proxy(cfg.vpn_gateway, cfg.vpn_interface)
            if vpn_proxy and not any(p.url == vpn_proxy for p in self._proxies):
                self._proxies.append(ProxyStats(url=vpn_proxy))
                self._cycle = itertools.cycle(self._proxies)

    @staticmethod
    def _build_vpn_proxy(gateway: str, interface: Optional[str]) -> Optional[str]:
        if gateway.startswith(("socks5://", "socks4://", "http://", "https://")):
            return gateway
        return f"socks5://{gateway}"

    async def get_connector(self) -> Optional[aiohttp.BaseConnector]:
        if not self.cfg.enabled or not self._proxies:
            return aiohttp.TCPConnector(
                limit=100,
                ttl_dns_cache=300,
                enable_cleanup_closed=True,
            )

        async with self._lock:
            self._call_count += 1
            now = time.monotonic()

            for _ in range(len(self._proxies)):
                proxy = next(self._cycle)
                if proxy.banned_until > now:
                    continue
                proxy.last_used = now
                return self._make_connector(proxy.url)

            best = min(self._proxies, key=lambda p: p.banned_until)
            best.banned_until = 0
            return self._make_connector(best.url)

    @staticmethod
    def _make_connector(proxy_url: str) -> aiohttp.BaseConnector:
        if proxy_url.startswith("socks5://"):
            return ProxyConnector.from_url(proxy_url, rdns=True)
        if proxy_url.startswith("socks4://"):
            return ProxyConnector.from_url(proxy_url)
        return ProxyConnector.from_url(proxy_url)

    async def report_success(self, proxy_url: str, latency: float):
        for p in self._proxies:
            if p.url == proxy_url:
                p.successes += 1
                p.avg_latency = (p.avg_latency * 0.8) + (latency * 0.2)
                break

    async def report_failure(self, proxy_url: str):
        for p in self._proxies:
            if p.url == proxy_url:
                p.failures += 1
                if p.failures >= 3:
                    p.banned_until = time.monotonic() + 60.0
                    p.failures = 0
                    log.warning("proxy banned 60s: %s", proxy_url)
                break

    def stats(self) -> list[dict]:
        return [
            {
                "url": p.url,
                "ok": p.successes,
                "fail": p.failures,
                "latency_ms": round(p.avg_latency * 1000),
            }
            for p in self._proxies
        ]


class HttpClient:
    """Async HTTP client with proxy rotation, retry, and connection pooling."""

    def __init__(self, pool: ProxyPool, cfg: ProxyConfig):
        self.pool = pool
        self.cfg = cfg
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            connector = await self.pool.get_connector()
            timeout = aiohttp.ClientTimeout(
                connect=self.cfg.connect_timeout,
                total=self.cfg.read_timeout,
            )
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                headers={"User-Agent": "MediaForge/1.0"},
            )
        return self._session

    async def get(self, url: str, headers: Optional[dict] = None,
                  params: Optional[dict] = None, retries: int = 3) -> bytes:
        last_err = None
        for attempt in range(retries):
            t0 = time.monotonic()
            try:
                session = await self._get_session()
                async with session.get(url, headers=headers, params=params) as resp:
                    resp.raise_for_status()
                    data = await resp.read()
                    elapsed = time.monotonic() - t0
                    if self.pool._proxies:
                        proxy_url = self.pool._proxies[0].url
                        await self.pool.report_success(proxy_url, elapsed)
                    return data
            except Exception as e:
                last_err = e
                elapsed = time.monotonic() - t0
                log.debug("request fail attempt %d: %s — %s", attempt, url[:100], e)
                if self.pool._proxies:
                    proxy_url = self.pool._proxies[0].url
                    await self.pool.report_failure(proxy_url)
                if attempt < retries - 1:
                    await self._rotate_session()
                    await asyncio.sleep(1.5 ** attempt)
        raise ConnectionError(f"all {retries} attempts failed for {url[:120]}: {last_err}")

    async def get_json(self, url: str, headers: Optional[dict] = None,
                       params: Optional[dict] = None, retries: int = 3) -> dict:
        import json
        data = await self.get(url, headers=headers, params=params, retries=retries)
        return json.loads(data)

    async def download(self, url: str, dest: str, retries: int = 3) -> str:
        data = await self.get(url, retries=retries)
        with open(dest, "wb") as f:
            f.write(data)
        return dest

    async def _rotate_session(self):
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None
