"""Gemini TTS — text-to-speech via Google AI Studio API.

Pure REST API. No browser, no BotGuard.
Multi-key rotation for high throughput.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
from typing import Optional

from .key_pool import KeyPool

log = logging.getLogger("mediaforge.gemini_tts")

BASE = "https://generativelanguage.googleapis.com/v1beta"
TTS_MODEL = "gemini-3.8-flash-tts"
TTS_FALLBACK_MODELS = ["gemini-3.8-flash-lite-tts", "gemini-3.1-flash-tts-preview"]

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

    models_to_try = [TTS_MODEL] + TTS_FALLBACK_MODELS

    async def _do(url):
        if http_client:
            return await http_client.post_json(url, json=body)
        import aiohttp
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=body) as r:
                r.raise_for_status()
                return await r.json()

    last_err = None
    for mi, model in enumerate(models_to_try):
        url = f"{BASE}/models/{model}:generateContent?key={api_key}"
        for attempt in range(3):
            try:
                if sem:
                    async with sem:
                        data = await _do(url)
                else:
                    data = await _do(url)
                if mi > 0:
                    log.info("TTS model fallback: %s -> %s", TTS_MODEL, model)
                last_err = None
                break
            except Exception as e:
                last_err = e
                err_str = str(e)
                is_server_err = any(c in err_str for c in ("503", "500", "502", "504"))
                if is_server_err and mi < len(models_to_try) - 1:
                    log.debug("TTS %s returned %s, trying fallback", model, err_str[:80])
                    break
                if attempt == 2:
                    if mi < len(models_to_try) - 1:
                        break
                    raise
                log.debug("gemini tts retry %d: %s", attempt + 1, e)
                await asyncio.sleep(1.5 * (attempt + 1))
        if last_err is None:
            break
    if last_err is not None:
        raise last_err

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
    api_key: str = "",
    voice: str = "Kore",
    concurrency: int = 20,
    http_client=None,
    cache=None,
    api_keys: Optional[list[str]] = None,
) -> list[str]:
    keys = list(api_keys) if api_keys else []
    if api_key and api_key not in keys:
        keys.insert(0, api_key)
    if not keys:
        raise ValueError("Gemini TTS requires at least one API key")

    key_pool = KeyPool(keys)
    sem = asyncio.Semaphore(concurrency)

    async def _gen(i: int, text: str) -> str:
        dest = os.path.join(output_dir, f"vo_{i:03d}.wav")
        if cache:
            cache_key = f"gemini-tts:{voice}:{text}"
            cached = cache.get(cache_key)
            if cached:
                import shutil
                shutil.copy2(cached, dest)
                return dest

        key = await key_pool.get()
        try:
            await gemini_tts(text, dest, key, voice, http_client, sem)
            await key_pool.report_success(key)
        except Exception as e:
            if "429" in str(e) or "Too Many" in str(e):
                await key_pool.report_error(key, cooldown=30.0)
            raise

        if cache:
            cache.put(f"gemini-tts:{voice}:{text}", dest)
        return dest

    tasks = [_gen(i, seg["vo"]) for i, seg in enumerate(segments)]
    paths = await asyncio.gather(*tasks)
    log.info("Gemini TTS: generated %d segments (%d keys, voice=%s)",
             len(paths), key_pool.size, voice)
    return list(paths)
