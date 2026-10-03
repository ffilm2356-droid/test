"""Pexels provider — images and videos, requires API key."""

from __future__ import annotations

import logging

from .base import MediaItem, MediaProvider, SearchResult

log = logging.getLogger("mediaforge.providers.pexels")


class PexelsProvider(MediaProvider):
    """Pexels API — photos and videos, free tier."""

    async def search(self, query: str, kind: str = "image",
                     limit: int = 30) -> SearchResult:
        if not self.cfg.api_key:
            return SearchResult([], query, kind, "pexels")

        headers = {"Authorization": self.cfg.api_key}

        if kind == "video":
            return await self._search_videos(query, limit, headers)
        return await self._search_photos(query, limit, headers)

    async def _search_photos(self, query: str, limit: int,
                             headers: dict) -> SearchResult:
        params = {"query": query, "per_page": str(min(limit, 30))}
        try:
            data = await self._api_get(
                f"{self.cfg.base_url}/search",
                headers=headers, params=params,
            )
        except Exception as e:
            log.warning("pexels photo search fail: %s — %s", query, e)
            return SearchResult([], query, "image", "pexels")

        items: list[MediaItem] = []
        for photo in data.get("photos", []):
            w = photo.get("width", 0)
            h = photo.get("height", 0)
            items.append(MediaItem(
                url=photo["src"]["large2x"],
                provider="pexels",
                title=photo.get("alt") or query,
                artist=photo.get("photographer", "Unknown"),
                license="Pexels License",
                page_url=photo.get("photographer_url") or photo["url"],
                width=w,
                height=h,
                kind="image",
                is_landscape=(w / max(h, 1)) >= 1.25,
            ))

        return SearchResult(items, query, "image", "pexels")

    async def _search_videos(self, query: str, limit: int,
                             headers: dict) -> SearchResult:
        params = {"query": query, "per_page": str(min(limit, 15))}
        try:
            data = await self._api_get(
                "https://api.pexels.com/videos/search",
                headers=headers, params=params,
            )
        except Exception as e:
            log.warning("pexels video search fail: %s — %s", query, e)
            return SearchResult([], query, "video", "pexels")

        items: list[MediaItem] = []
        for video in data.get("videos", []):
            w = video.get("width", 0)
            h = video.get("height", 0)
            if w < 640 or (w / max(h, 1)) < 1.3:
                continue

            files = video.get("video_files", [])
            mp4s = [
                f for f in files
                if f.get("file_type") == "video/mp4"
                and 360 <= (f.get("height") or 0) <= 1080
            ]
            if not mp4s:
                continue
            mp4s.sort(key=lambda x: abs((x.get("height") or 0) - 720))
            best = mp4s[0]

            user = video.get("user", {})
            items.append(MediaItem(
                url=best["link"],
                provider="pexels",
                title=f"Pexels video {video['id']}",
                artist=user.get("name", "Unknown"),
                license="Pexels License",
                page_url=video.get("url", ""),
                width=best.get("width", w),
                height=best.get("height", h),
                kind="video",
                is_landscape=True,
            ))

        return SearchResult(items, query, "video", "pexels")

    async def download(self, item: MediaItem, dest: str) -> str:
        return await self._download_file(item.url, dest)
