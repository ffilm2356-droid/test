"""Wikimedia Commons provider — async rewrite of original Commons class."""

from __future__ import annotations

import html
import logging
import re
from typing import Optional

from .base import MediaItem, MediaProvider, SearchResult

log = logging.getLogger("mediaforge.providers.commons")

BLACKLIST = re.compile(
    r"propaganda|\bwar\b|\bAI\b|gameplay|star wars|harry potter|logo|icon|map of|diagram|"
    r"chart|graph|svg|pdf|sign language|captation|nude|weapon|protest",
    re.I,
)

BASE_URL = "https://commons.wikimedia.org/w/api.php"


class CommonsProvider(MediaProvider):
    """Wikimedia Commons — free, no API key needed."""

    async def search(self, query: str, kind: str = "image",
                     limit: int = 30) -> SearchResult:
        k = "v" if kind == "video" else "i"
        ft = "filetype:video" if k == "v" else "filetype:bitmap"
        params = dict(
            action="query", format="json", generator="search",
            gsrsearch=f"{query} {ft}", gsrnamespace="6", gsrlimit=str(limit),
        )
        if k == "v":
            params.update(prop="videoinfo",
                          viprop="url|size|mime|extmetadata|derivatives")
        else:
            params.update(prop="imageinfo",
                          iiprop="url|size|mime|extmetadata", iiurlwidth="1920")

        try:
            data = await self._api_get(BASE_URL, params=params)
        except Exception as e:
            log.warning("commons search fail: %s — %s", query, e)
            return SearchResult([], query, kind, "commons")

        pages = sorted(
            (data.get("query", {}) or {}).get("pages", {}).values(),
            key=lambda p: p.get("index", 0),
        )

        items: list[MediaItem] = []
        for p in pages:
            info = (p.get("videoinfo") or p.get("imageinfo") or [None])[0]
            if not info:
                continue

            title = p["title"][5:]
            if BLACKLIST.search(title):
                continue

            meta = info.get("extmetadata", {})
            lic = meta.get("LicenseShortName", {}).get("value", "")
            if not lic or "fair use" in lic.lower():
                continue

            artist = re.sub(
                "<[^>]+>", "",
                html.unescape(meta.get("Artist", {}).get("value", "")),
            ).strip()[:120]

            w, h = info.get("width", 0), info.get("height", 0)

            if k == "v":
                ders = [
                    x for x in info.get("derivatives", [])
                    if "webm" in x.get("type", "") or "mp4" in x.get("type", "")
                ]
                ders = [
                    x for x in ders
                    if 360 <= int(x.get("height", 0) or 0) <= 1080
                ]
                if not ders or w < 640 or (w / max(h, 1)) < 1.3:
                    continue
                ders.sort(key=lambda x: abs(int(x.get("height", 0)) - 720))
                url = ders[0]["src"]
            else:
                if w < 900 or info.get("mime") not in (
                    "image/jpeg", "image/png", "image/webp"
                ):
                    continue
                url = info.get("thumburl") or info["url"]

            items.append(MediaItem(
                url=url,
                provider="commons",
                title=title,
                artist=artist,
                license=lic,
                page_url=info.get("descriptionurl", ""),
                width=w,
                height=h,
                kind=kind,
                is_landscape=(w / max(h, 1)) >= 1.25,
            ))

        if kind == "image":
            items.sort(key=lambda x: not x.is_landscape)

        return SearchResult(items, query, kind, "commons")

    async def download(self, item: MediaItem, dest: str) -> str:
        return await self._download_file(item.url, dest)
