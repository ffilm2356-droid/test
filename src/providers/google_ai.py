"""Google AI Studio provider — Imagen 3 (images) + Veo 2 (videos).

Pure REST API. No browser, no BotGuard, no captcha.
Uses generativelanguage.googleapis.com endpoints directly.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional

from ..config import ProviderConfig
from ..proxy import HttpClient
from ..cache import MediaCache
from ..rate_limiter import TokenBucket
from .base import MediaItem, SearchResult

log = logging.getLogger("mediaforge.google_ai")

BASE = "https://generativelanguage.googleapis.com/v1beta"
IMAGEN_MODEL = "imagen-3.0-generate-002"
VEO_MODEL = "veo-2.0-generate-001"


class GoogleAIProvider:
    """Generative provider: creates images/videos from prompts via Google AI Studio API."""

    def __init__(self, cfg: ProviderConfig, http: HttpClient, cache: MediaCache,
                 concurrency: int = 50):
        self.cfg = cfg
        self.http = http
        self.cache = cache
        self._limiter = TokenBucket(cfg.rate_limit)
        self._sem = asyncio.Semaphore(concurrency)
        self._gen_count = 0

    @property
    def api_key(self) -> str:
        return self.cfg.api_key

    async def _post_json(self, url: str, body: dict) -> dict:
        await self._limiter.acquire()
        import aiohttp
        async with self._sem:
            return await self.http.post_json(url, json=body)

    # --- Image generation (Imagen 3) ---

    async def generate_image(self, prompt: str, aspect: str = "16:9",
                             count: int = 1) -> list[bytes]:
        url = f"{BASE}/models/{IMAGEN_MODEL}:predict?key={self.api_key}"
        body = {
            "instances": [{"prompt": prompt}],
            "parameters": {
                "sampleCount": count,
                "aspectRatio": aspect,
                "safetyFilterLevel": "BLOCK_ONLY_HIGH",
            },
        }
        data = await self._post_json(url, body)
        images = []
        for pred in data.get("predictions", []):
            b64 = pred.get("bytesBase64Encoded", "")
            if b64:
                images.append(base64.b64decode(b64))
        return images

    async def generate_image_to_file(self, prompt: str, dest: str,
                                     aspect: str = "16:9") -> str:
        cache_key = f"imagen:{prompt}:{aspect}"
        cached = self.cache.get(cache_key)
        if cached:
            import shutil
            shutil.copy2(cached, dest)
            return dest

        imgs = await self.generate_image(prompt, aspect, count=1)
        if not imgs:
            raise RuntimeError(f"Imagen returned no images for: {prompt[:60]}")

        with open(dest, "wb") as f:
            f.write(imgs[0])
        self.cache.put(cache_key, dest)
        self._gen_count += 1
        return dest

    # --- Video generation (Veo 2) ---

    async def generate_video(self, prompt: str, duration: int = 5,
                             aspect: str = "16:9",
                             ref_image: Optional[bytes] = None) -> bytes:
        url = f"{BASE}/models/{VEO_MODEL}:predictLongRunning?key={self.api_key}"
        instance: dict = {"prompt": prompt}
        if ref_image:
            instance["image"] = {
                "bytesBase64Encoded": base64.b64encode(ref_image).decode(),
            }
        body = {
            "instances": [instance],
            "parameters": {
                "aspectRatio": aspect,
                "durationSeconds": duration,
                "sampleCount": 1,
            },
        }
        data = await self._post_json(url, body)
        op_name = data.get("name")
        if not op_name:
            raise RuntimeError(f"Veo returned no operation: {data}")

        return await self._poll_operation(op_name)

    async def _poll_operation(self, op_name: str, timeout: float = 300) -> bytes:
        url = f"{BASE}/{op_name}?key={self.api_key}"
        deadline = time.monotonic() + timeout
        delay = 2.0
        while time.monotonic() < deadline:
            await asyncio.sleep(delay)
            data = await self.http.get_json(url)
            if data.get("done"):
                resp = data.get("response", {})
                for vid in resp.get("predictions", []):
                    b64 = vid.get("bytesBase64Encoded", "")
                    if b64:
                        return base64.b64decode(b64)
                err = data.get("error", {})
                raise RuntimeError(
                    f"Veo operation failed: {err.get('message', 'unknown')}")
            delay = min(delay * 1.3, 10.0)
        raise TimeoutError(f"Veo operation timed out after {timeout}s: {op_name}")

    async def generate_video_to_file(self, prompt: str, dest: str,
                                     duration: int = 5, aspect: str = "16:9",
                                     ref_image_path: Optional[str] = None) -> str:
        cache_key = f"veo:{prompt}:{duration}:{aspect}"
        cached = self.cache.get(cache_key)
        if cached:
            import shutil
            shutil.copy2(cached, dest)
            return dest

        ref_bytes = None
        if ref_image_path and os.path.exists(ref_image_path):
            with open(ref_image_path, "rb") as f:
                ref_bytes = f.read()

        video_bytes = await self.generate_video(prompt, duration, aspect, ref_bytes)
        with open(dest, "wb") as f:
            f.write(video_bytes)
        self.cache.put(cache_key, dest)
        self._gen_count += 1
        return dest

    # --- Search interface (compatibility with MediaPool) ---

    async def search(self, query: str, kind: str = "image",
                     limit: int = 1) -> SearchResult:
        self._gen_count += 1
        uid = f"google-ai-gen-{self._gen_count}"
        item = MediaItem(
            url=f"generate://{kind}/{uid}",
            provider="google_ai",
            title=query[:80],
            artist="Google AI (Imagen/Veo)",
            license="Google AI Studio ToS",
            page_url="https://aistudio.google.com",
            width=1280 if kind == "image" else 1920,
            height=720 if kind == "image" else 1080,
            kind=kind,
            is_landscape=True,
            metadata={"prompt": query, "generated": True},
        )
        return SearchResult(items=[item], query=query, kind=kind,
                            provider="google_ai")

    async def download(self, item: MediaItem, dest: str) -> str:
        prompt = item.metadata.get("prompt", item.title)
        if item.kind == "video":
            return await self.generate_video_to_file(prompt, dest + ".mp4")
        return await self.generate_image_to_file(prompt, dest + ".jpg")


# --- Batch helpers for speed ---

async def batch_generate_images(
    provider: GoogleAIProvider,
    prompts: list[str],
    output_dir: str,
    aspect: str = "16:9",
) -> list[str]:
    os.makedirs(output_dir, exist_ok=True)
    tasks = []
    for i, p in enumerate(prompts):
        dest = os.path.join(output_dir, f"img_{i:04d}.jpg")
        tasks.append(provider.generate_image_to_file(p, dest, aspect))
    return await asyncio.gather(*tasks)


async def batch_generate_videos(
    provider: GoogleAIProvider,
    prompts: list[dict],
    output_dir: str,
    aspect: str = "16:9",
) -> list[str]:
    os.makedirs(output_dir, exist_ok=True)
    tasks = []
    for i, p in enumerate(prompts):
        dest = os.path.join(output_dir, f"vid_{i:04d}.mp4")
        prompt = p if isinstance(p, str) else p["prompt"]
        ref = p.get("ref_image") if isinstance(p, dict) else None
        duration = p.get("duration", 5) if isinstance(p, dict) else 5
        tasks.append(provider.generate_video_to_file(
            prompt, dest, duration, aspect, ref))
    return await asyncio.gather(*tasks)
