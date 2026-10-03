"""Configuration — API keys, proxy settings, rate limits, paths."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class ProxyConfig:
    enabled: bool = False
    proxies: list[str] = field(default_factory=list)
    rotate_every: int = 25
    vpn_gateway: Optional[str] = None
    vpn_interface: Optional[str] = None
    connect_timeout: float = 10.0
    read_timeout: float = 30.0


@dataclass
class RateLimit:
    calls_per_second: float = 5.0
    burst: int = 10


@dataclass
class ProviderConfig:
    name: str
    api_key: str = ""
    base_url: str = ""
    rate_limit: RateLimit = field(default_factory=RateLimit)
    enabled: bool = True


@dataclass
class Config:
    cache_dir: Path = field(default_factory=lambda: Path.home() / ".mediaforge" / "cache")
    tmp_dir: Path = field(default_factory=lambda: Path("/tmp/mediaforge"))
    max_workers: int = 8
    ffmpeg_threads: int = 2
    width: int = 1280
    height: int = 720
    fps: int = 30
    oversize: float = 1.14

    proxy: ProxyConfig = field(default_factory=ProxyConfig)

    unsplash: ProviderConfig = field(default_factory=lambda: ProviderConfig(
        name="unsplash",
        base_url="https://api.unsplash.com",
        rate_limit=RateLimit(calls_per_second=30, burst=50),
    ))
    pexels: ProviderConfig = field(default_factory=lambda: ProviderConfig(
        name="pexels",
        base_url="https://api.pexels.com/v1",
        rate_limit=RateLimit(calls_per_second=20, burst=40),
    ))
    pixabay: ProviderConfig = field(default_factory=lambda: ProviderConfig(
        name="pixabay",
        base_url="https://pixabay.com/api",
        rate_limit=RateLimit(calls_per_second=10, burst=20),
    ))
    wikimedia: ProviderConfig = field(default_factory=lambda: ProviderConfig(
        name="wikimedia",
        base_url="https://commons.wikimedia.org/w/api.php",
        rate_limit=RateLimit(calls_per_second=5, burst=10),
    ))

    tts_voice: str = "en-US-ChristopherNeural"
    tts_rate: str = "+6%"
    tts_concurrency: int = 6

    @classmethod
    def from_env(cls) -> Config:
        cfg = cls()
        cfg.unsplash.api_key = os.getenv("UNSPLASH_ACCESS_KEY", "")
        cfg.pexels.api_key = os.getenv("PEXELS_API_KEY", "")
        cfg.pixabay.api_key = os.getenv("PIXABAY_API_KEY", "")

        proxy_list = os.getenv("MEDIAFORGE_PROXIES", "")
        if proxy_list:
            cfg.proxy.enabled = True
            cfg.proxy.proxies = [p.strip() for p in proxy_list.split(",") if p.strip()]

        vpn_gw = os.getenv("MEDIAFORGE_VPN_GATEWAY", "")
        if vpn_gw:
            cfg.proxy.vpn_gateway = vpn_gw
            cfg.proxy.vpn_interface = os.getenv("MEDIAFORGE_VPN_INTERFACE", "tun0")

        cache = os.getenv("MEDIAFORGE_CACHE_DIR", "")
        if cache:
            cfg.cache_dir = Path(cache)

        workers = os.getenv("MEDIAFORGE_WORKERS", "")
        if workers:
            cfg.max_workers = int(workers)

        return cfg

    @classmethod
    def from_dict(cls, d: dict) -> Config:
        cfg = cls.from_env()
        if "proxy" in d:
            p = d["proxy"]
            cfg.proxy.enabled = p.get("enabled", cfg.proxy.enabled)
            cfg.proxy.proxies = p.get("proxies", cfg.proxy.proxies)
            cfg.proxy.rotate_every = p.get("rotate_every", cfg.proxy.rotate_every)
            cfg.proxy.vpn_gateway = p.get("vpn_gateway", cfg.proxy.vpn_gateway)
            cfg.proxy.vpn_interface = p.get("vpn_interface", cfg.proxy.vpn_interface)
        for key in ("unsplash", "pexels", "pixabay", "wikimedia"):
            if key in d:
                prov = getattr(cfg, key)
                prov.api_key = d[key].get("api_key", prov.api_key)
                prov.enabled = d[key].get("enabled", prov.enabled)
        for key in ("width", "height", "fps", "max_workers", "tts_voice", "tts_rate"):
            if key in d:
                setattr(cfg, key, d[key])
        return cfg
