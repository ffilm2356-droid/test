"""Gemini chat/agent — Google AI Studio API.

Pure REST API. No browser, no BotGuard.
Supports text generation, multimodal input, and structured output.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
from typing import Optional

log = logging.getLogger("mediaforge.gemini")

BASE = "https://generativelanguage.googleapis.com/v1beta"


class GeminiClient:
    """Async Gemini chat client via REST API."""

    def __init__(self, api_key: str, model: str = "gemini-2.0-flash",
                 http_client=None, concurrency: int = 50):
        self.api_key = api_key
        self.model = model
        self.http = http_client
        self._sem = asyncio.Semaphore(concurrency)

    async def generate(self, prompt: str, system: Optional[str] = None,
                       temperature: float = 0.7, max_tokens: int = 2048,
                       json_mode: bool = False) -> str:
        url = f"{BASE}/models/{self.model}:generateContent?key={self.api_key}"
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

        async with self._sem:
            if self.http:
                data = await self.http.post_json(url, json=body)
            else:
                import aiohttp
                async with aiohttp.ClientSession() as s:
                    async with s.post(url, json=body) as r:
                        r.raise_for_status()
                        data = await r.json()

        candidates = data.get("candidates", [])
        if not candidates:
            raise RuntimeError(f"Gemini returned no candidates")
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

        url = f"{BASE}/models/{self.model}:generateContent?key={self.api_key}"
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

        async with self._sem:
            if self.http:
                data = await self.http.post_json(url, json=body)
            else:
                import aiohttp
                async with aiohttp.ClientSession() as s:
                    async with s.post(url, json=body) as r:
                        r.raise_for_status()
                        data = await r.json()

        candidates = data.get("candidates", [])
        if not candidates:
            raise RuntimeError("Gemini returned no candidates")
        parts = candidates[0].get("content", {}).get("parts", [])
        return "".join(p.get("text", "") for p in parts)

    async def batch_generate(self, prompts: list[str],
                             system: Optional[str] = None) -> list[str]:
        tasks = [self.generate(p, system=system) for p in prompts]
        return await asyncio.gather(*tasks)
