"""Pixabay provider — images and videos, requires API key."""

from __future__ import annotations

import logging

from .base import MediaItem, MediaProvider, SearchResult

log = logging.getLogger("mediaforge.providers.pixabay")


class PixabayProvider(MediaProvider):
    """Pixabay API — photos and videos, 100 req/min free."""

    async def search(self, query: str, kind: str = "image",
                     limit: int = 30) -> SearchResult:
        if not self.cfg.api_key:
            return SearchResult([], query, kind, "pixabay")

        if kind == "video":
            return await self._search_videos(query, limit)
        return await self._search_photos(query, limit)

    async def _search_photos(self, query: str, limit: int) -> SearchResult:
        params = {
            "key": self.cfg.api_key,
            "q": query,
            "per_page": str(min(limit, 30)),
            "image_type": "photo",
            "orientation": "horizontal",
            "min_width": "900",
            "safesearch": "true",
        }
        try:
            data = await self._api_get(self.cfg.base_url, params=params)
        except Exception as e:
            log.warning("pixabay photo search fail: %s — %s", query, e)
            return SearchResult([], query, "image", "pixabay")

        items: list[MediaItem] = []
        for hit in data.get("hits", []):
            w = hit.get("imageWidth", 0)
            h = hit.get("imageHeight", 0)
            items.append(MediaItem(
                url=hit.get("largeImageURL", hit.get("webformatURL", "")),
                provider="pixabay",
                title=hit.get("tags", query),
                artist=hit.get("user", "Unknown"),
                license="Pixabay License",
                page_url=hit.get("pageURL", ""),
                width=w,
                height=h,
                kind="image",
                is_landscape=(w / max(h, 1)) >= 1.25,
            ))

        return SearchResult(items, query, "image", "pixabay")

    async def _search_videos(self, query: str, limit: int) -> SearchResult:
        params = {
            "key": self.cfg.api_key,
            "q": query,
            "per_page": str(min(limit, 15)),
            "safesearch": "true",
        }
        try:
            data = await self._api_get(
                "https://pixabay.com/api/videos/", params=params,
            )
        except Exception as e:
            log.warning("pixabay video search fail: %s — %s", query, e)
            return SearchResult([], query, "video", "pixabay")

        items: list[MediaItem] = []
        for hit in data.get("hits", []):
            videos = hit.get("videos", {})
            best = None
            for quality in ("medium", "small", "large"):
                v = videos.get(quality)
                if v and v.get("url") and 360 <= (v.get("height") or 0) <= 1080:
                    best = v
                    break
            if not best:
                continue

            items.append(MediaItem(
                url=best["url"],
                provider="pixabay",
                title=hit.get("tags", query),
                artist=hit.get("user", "Unknown"),
                license="Pixabay License",
                page_url=hit.get("pageURL", ""),
                width=best.get("width", 0),
                height=best.get("height", 0),
                kind="video",
                is_landscape=True,
            ))

        return SearchResult(items, query, "video", "pixabay")

    async def download(self, item: MediaItem, dest: str) -> str:
        return await self._download_file(item.url, dest)
