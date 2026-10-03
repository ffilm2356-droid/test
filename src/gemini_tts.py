"""Gemini TTS — text-to-speech via Google AI Studio API.

Pure REST API. No browser, no BotGuard.
Uses gemini-2.5-flash-preview-tts model for high-quality neural TTS.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
from typing import Optional

log = logging.getLogger("mediaforge.gemini_tts")

BASE = "https://generativelanguage.googleapis.com/v1beta"
TTS_MODEL = "gemini-3.8-flash-tts"

VOICES = [
    "Zephyr", "Puck", "Charon", "Kore", "Fenrir", "Leda",
    "Orus", "Aoede", "Callirrhoe", "Autonoe",
]


async def gemini_tts(
    text: str,
    dest: str,
    api_key: str,
    voice: str = "Kore",
    http_client=None,
    sem: Optional[asyncio.Semaphore] = None,
) -> str:
    url = f"{BASE}/models/{TTS_MODEL}:generateContent?key={api_key}"
    body = {
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {"voiceName": voice},
                },
            },
        },
    }

    async def _do():
        if http_client:
            data = await http_client.post_json(url, json=body)
        else:
            import aiohttp
            async with aiohttp.ClientSession() as s:
                async with s.post(url, json=body) as r:
                    r.raise_for_status()
                    data = await r.json()
        return data

    for attempt in range(3):
        try:
            if sem:
                async with sem:
                    data = await _do()
            else:
                data = await _do()
            break
        except Exception as e:
            if attempt == 2:
                raise
            log.debug("gemini tts retry %d: %s", attempt + 1, e)
            await asyncio.sleep(1.5 * (attempt + 1))

    candidates = data.get("candidates", [])
    if not candidates:
        raise RuntimeError(f"Gemini TTS returned no candidates for: {text[:60]}")

    parts = candidates[0].get("content", {}).get("parts", [])
    for part in parts:
        ib = part.get("inlineData", {})
        if ib.get("data"):
            audio_bytes = base64.b64decode(ib["data"])
            with open(dest, "wb") as f:
                f.write(audio_bytes)
            return dest

    raise RuntimeError("Gemini TTS returned no audio data")


async def generate_tts_gemini(
    segments: list[dict],
    output_dir: str,
    api_key: str,
    voice: str = "Kore",
    concurrency: int = 20,
    http_client=None,
    cache=None,
) -> list[str]:
    sem = asyncio.Semaphore(concurrency)
    paths = []

    async def _gen(i: int, text: str) -> str:
        dest = os.path.join(output_dir, f"vo_{i:03d}.wav")
        if cache:
            cache_key = f"gemini-tts:{voice}:{text}"
            cached = cache.get(cache_key)
            if cached:
                import shutil
                shutil.copy2(cached, dest)
                return dest

        await gemini_tts(text, dest, api_key, voice, http_client, sem)

        if cache:
            cache.put(f"gemini-tts:{voice}:{text}", dest)
        return dest

    tasks = []
    for i, seg in enumerate(segments):
        tasks.append(_gen(i, seg["vo"]))

    paths = await asyncio.gather(*tasks)
    log.info("Gemini TTS: generated %d segments (voice=%s)", len(paths), voice)
    return list(paths)
