"""Unsplash provider — images only, requires API key."""

from __future__ import annotations

import logging

from .base import MediaItem, MediaProvider, SearchResult

log = logging.getLogger("mediaforge.providers.unsplash")


class UnsplashProvider(MediaProvider):
    """Unsplash API — high-quality photos, free tier 50 req/hr."""

    async def search(self, query: str, kind: str = "image",
                     limit: int = 30) -> SearchResult:
        if kind == "video":
            return SearchResult([], query, kind, "unsplash")
        if not self.cfg.api_key:
            return SearchResult([], query, kind, "unsplash")

        headers = {"Authorization": f"Client-ID {self.cfg.api_key}"}
        params = {
            "query": query,
            "per_page": str(min(limit, 30)),
            "orientation": "landscape",
        }

        try:
            data = await self._api_get(
                f"{self.cfg.base_url}/search/photos",
                headers=headers, params=params,
            )
        except Exception as e:
            log.warning("unsplash search fail: %s — %s", query, e)
            return SearchResult([], query, kind, "unsplash")

        items: list[MediaItem] = []
        for photo in data.get("results", []):
            w = photo.get("width", 0)
            h = photo.get("height", 0)
            user = photo.get("user", {})

            items.append(MediaItem(
                url=photo["urls"]["regular"],
                provider="unsplash",
                title=photo.get("description") or photo.get("alt_description") or query,
                artist=user.get("name", "Unknown"),
                license="Unsplash License",
                page_url=photo["links"]["html"] + "?utm_source=mediaforge&utm_medium=referral",
                width=w,
                height=h,
                kind="image",
                is_landscape=(w / max(h, 1)) >= 1.25,
                metadata={"unsplash_id": photo["id"]},
            ))

        return SearchResult(items, query, kind, "unsplash")

    async def download(self, item: MediaItem, dest: str) -> str:
        if self.cfg.api_key:
            dl_url = item.url
            try:
                await self._api_get(
                    f"{self.cfg.base_url}/photos/{item.metadata.get('unsplash_id')}/download",
                    headers={"Authorization": f"Client-ID {self.cfg.api_key}"},
                )
            except Exception:
                pass
        return await self._download_file(item.url, dest)
