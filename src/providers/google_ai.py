"""Google AI Studio provider — image gen + video gen (Veo).

Pure REST API. No browser, no BotGuard, no captcha.
Uses generativelanguage.googleapis.com endpoints directly.
Multi-key rotation for high throughput (50K+ images/day).
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import time
from typing import Optional

from ..config import ProviderConfig
from ..proxy import HttpClient
from ..cache import MediaCache
from ..rate_limiter import TokenBucket
from ..key_pool import KeyPool
from .base import MediaItem, SearchResult

log = logging.getLogger("mediaforge.google_ai")

BASE = "https://generativelanguage.googleapis.com/v1beta"
IMAGE_MODELS = [
    "gemini-3.1-flash-image",
    "gemini-3-pro-image",
    "gemini-2.5-flash-image",
    "gemini-3.1-flash-lite-image",
]
IMAGE_MODEL = IMAGE_MODELS[0]
VEO_MODEL = "veo-3.1-generate-preview"
CHAT_MODEL = "gemini-3.8-flash"


class GoogleAIProvider:
    """Generative provider: creates images/videos from prompts via Google AI Studio API.

    Supports multi-key rotation: pass multiple keys via api_keys config
    to spread quota across accounts (2-3 keys = 50K+ images/day).
    """

    def __init__(self, cfg: ProviderConfig, http: HttpClient, cache: MediaCache,
                 concurrency: int = 50):
        self.cfg = cfg
        self.http = http
        self.cache = cache
        self._limiter = TokenBucket(cfg.rate_limit)
        self._sem = asyncio.Semaphore(concurrency)
        self._gen_count = 0

        keys = list(cfg.api_keys) if cfg.api_keys else []
        if cfg.api_key and cfg.api_key not in keys:
            keys.insert(0, cfg.api_key)
        if not keys:
            raise ValueError("GoogleAIProvider requires at least one API key")
        self._key_pool = KeyPool(keys)
        log.info("GoogleAI: %d API key(s), concurrency=%d", len(keys), concurrency)

    async def _get_key(self) -> str:
        return await self._key_pool.get()

    async def _post_json(self, url_template: str, body: dict, retries: int = 5) -> dict:
        await self._limiter.acquire()
        last_err = None
        for attempt in range(retries):
            key = await self._get_key()
            url = url_template.replace("{KEY}", key)
            try:
                async with self._sem:
                    data = await self.http.post_json(url, json=body, retries=1)
                await self._key_pool.report_success(key)
                return data
            except Exception as e:
                last_err = e
                err_str = str(e)
                is_rate_limit = "429" in err_str or "Too Many" in err_str
                is_server_err = any(c in err_str for c in ("503", "500", "502", "504"))
                if is_rate_limit:
                    await self._key_pool.report_error(key, cooldown=30.0)
                    log.debug("key ...%s rate-limited, rotating", key[-6:])
                elif is_server_err:
                    log.debug("server error attempt %d: %s", attempt, err_str[:100])
                else:
                    log.debug("POST fail attempt %d: %s", attempt, err_str[:100])
                if attempt < retries - 1:
                    if is_rate_limit:
                        delay = 2.0
                    elif is_server_err:
                        delay = 3.0 * (attempt + 1)
                    else:
                        delay = 1.5 ** attempt
                    await asyncio.sleep(delay)
        raise ConnectionError(f"GoogleAI POST failed after {retries} attempts: {last_err}")

    # --- Image generation (generateContent with IMAGE modality) ---

    async def generate_image(self, prompt: str, count: int = 1) -> list[bytes]:
        body = {
            "contents": [{"parts": [{"text": f"Generate an image: {prompt}"}]}],
            "generationConfig": {
                "responseModalities": ["IMAGE", "TEXT"],
            },
        }
        last_err = None
        for model in IMAGE_MODELS:
            url = f"{BASE}/models/{model}:generateContent?key={{KEY}}"
            try:
                data = await self._post_json(url, body)
                images = []
                for cand in data.get("candidates", []):
                    for part in cand.get("content", {}).get("parts", []):
                        ib = part.get("inlineData", {})
                        if ib.get("data") and ib.get("mimeType", "").startswith("image/"):
                            images.append(base64.b64decode(ib["data"]))
                if images:
                    return images
            except Exception as e:
                last_err = e
                err_str = str(e)
                if "limit: 0" in err_str or ("429" in err_str and "quota" in err_str.lower()):
                    log.debug("image model %s quota exhausted, trying next", model)
                    continue
                raise
        if last_err:
            raise last_err
        raise RuntimeError("No image generation models available")

    async def generate_image_to_file(self, prompt: str, dest: str,
                                     aspect: str = "16:9") -> str:
        cache_key = f"imagen:{prompt}:{aspect}"
        cached = self.cache.get(cache_key)
        if cached:
            import shutil
            shutil.copy2(cached, dest)
            return dest

        imgs = await self.generate_image(prompt, count=1)
        if not imgs:
            raise RuntimeError(f"Image gen returned no images for: {prompt[:60]}")

        with open(dest, "wb") as f:
            f.write(imgs[0])
        self.cache.put(cache_key, dest)
        self._gen_count += 1
        return dest

    # --- Video generation (Veo 3.1) ---

    async def generate_video(self, prompt: str, duration: int = 5,
                             aspect: str = "16:9",
                             ref_image: Optional[bytes] = None) -> bytes:
        url = f"{BASE}/models/{VEO_MODEL}:predictLongRunning?key={{KEY}}"
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
        deadline = time.monotonic() + timeout
        delay = 2.0
        while time.monotonic() < deadline:
            await asyncio.sleep(delay)
            key = await self._get_key()
            url = f"{BASE}/{op_name}?key={key}"
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
            artist="Google AI",
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

    def key_stats(self) -> list[dict]:
        return self._key_pool.stats()


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
