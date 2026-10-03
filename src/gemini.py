"""Gemini chat/agent — Google AI Studio API.

Pure REST API. No browser, no BotGuard.
Multi-key rotation for high throughput.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import Optional

from .key_pool import KeyPool

log = logging.getLogger("mediaforge.gemini")

BASE = "https://generativelanguage.googleapis.com/v1beta"


class GeminiClient:
    """Async Gemini chat client via REST API with multi-key rotation."""

    def __init__(self, api_key: str = "", model: str = "gemini-3.8-flash",
                 http_client=None, concurrency: int = 50,
                 api_keys: Optional[list[str]] = None):
        self.model = model
        self.http = http_client
        self._sem = asyncio.Semaphore(concurrency)

        keys = list(api_keys) if api_keys else []
        if api_key and api_key not in keys:
            keys.insert(0, api_key)
        if not keys:
            raise ValueError("GeminiClient requires at least one API key")
        self._key_pool = KeyPool(keys)

    async def _call_api(self, body: dict) -> dict:
        for attempt in range(3):
            key = await self._key_pool.get()
            url = f"{BASE}/models/{self.model}:generateContent?key={key}"
            try:
                async with self._sem:
                    if self.http:
                        data = await self.http.post_json(url, json=body, retries=1)
                    else:
                        import aiohttp
                        async with aiohttp.ClientSession() as s:
                            async with s.post(url, json=body) as r:
                                r.raise_for_status()
                                data = await r.json()
                await self._key_pool.report_success(key)
                return data
            except Exception as e:
                if "429" in str(e):
                    await self._key_pool.report_error(key, cooldown=30.0)
                if attempt == 2:
                    raise
                await asyncio.sleep(2.0 * (attempt + 1))

    async def generate(self, prompt: str, system: Optional[str] = None,
                       temperature: float = 0.7, max_tokens: int = 2048,
                       json_mode: bool = False) -> str:
        contents = [{"parts": [{"text": prompt}]}]
        body: dict = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if json_mode:
            body["generationConfig"]["responseMimeType"] = "application/json"

        data = await self._call_api(body)
        candidates = data.get("candidates", [])
        if not candidates:
            raise RuntimeError("Gemini returned no candidates")
        parts = candidates[0].get("content", {}).get("parts", [])
        return "".join(p.get("text", "") for p in parts)

    async def generate_with_image(self, prompt: str, image_path: str,
                                  temperature: float = 0.7) -> str:
        with open(image_path, "rb") as f:
            img_bytes = f.read()
        mime = "image/jpeg"
        if image_path.endswith(".png"):
            mime = "image/png"
        elif image_path.endswith(".webp"):
            mime = "image/webp"

        body = {
            "contents": [{
                "parts": [
                    {"text": prompt},
                    {"inlineData": {
                        "mimeType": mime,
                        "data": base64.b64encode(img_bytes).decode(),
                    }},
                ],
            }],
            "generationConfig": {"temperature": temperature},
        }

        data = await self._call_api(body)
        candidates = data.get("candidates", [])
        if not candidates:
            raise RuntimeError("Gemini returned no candidates")
        parts = candidates[0].get("content", {}).get("parts", [])
        return "".join(p.get("text", "") for p in parts)

    async def batch_generate(self, prompts: list[str],
                             system: Optional[str] = None) -> list[str]:
        tasks = [self.generate(p, system=system) for p in prompts]
        return await asyncio.gather(*tasks)
