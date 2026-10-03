"""Abstract base class for media providers + shared types."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..config import ProviderConfig
from ..proxy import HttpClient
from ..cache import MediaCache
from ..rate_limiter import TokenBucket

log = logging.getLogger("mediaforge.providers")


@dataclass
class MediaItem:
    url: str
    provider: str
    title: str
    artist: str
    license: str
    page_url: str
    width: int
    height: int
    kind: str  # "image" | "video"
    is_landscape: bool = True
    metadata: dict = field(default_factory=dict)


@dataclass
class SearchResult:
    items: list[MediaItem]
    query: str
    kind: str
    provider: str
    cached: bool = False


class MediaProvider(ABC):
    """Abstract base for all media providers."""

    def __init__(self, cfg: ProviderConfig, http: HttpClient, cache: MediaCache):
        self.cfg = cfg
        self.http = http
        self.cache = cache
        self._limiter = TokenBucket(cfg.rate_limit)
        self._used: set[str] = set()

    @abstractmethod
    async def search(self, query: str, kind: str = "image",
                     limit: int = 30) -> SearchResult:
        ...

    @abstractmethod
    async def download(self, item: MediaItem, dest: str) -> str:
        ...

    async def _api_get(self, url: str, headers: Optional[dict] = None,
                       params: Optional[dict] = None) -> dict:
        await self._limiter.acquire()
        return await self.http.get_json(url, headers=headers, params=params)

    async def _download_file(self, url: str, dest: str) -> str:
        cached = self.cache.get(url)
        if cached:
            import shutil
            shutil.copy2(cached, dest)
            return dest
        await self._limiter.acquire()
        path = await self.http.download(url, dest)
        self.cache.put(url, dest)
        return path

    def take(self, results: list[MediaItem]) -> Optional[MediaItem]:
        for item in results:
            if item.url not in self._used:
                self._used.add(item.url)
                return item
        return None
