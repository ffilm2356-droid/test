"""ElevenLabs TTS — text-to-speech via ElevenLabs API.

Pure REST API. No browser, no captcha.
Supports multi-key rotation for high throughput.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from .key_pool import KeyPool

log = logging.getLogger("mediaforge.elevenlabs_tts")

BASE = "https://api.elevenlabs.io/v1"

VOICES = {
    "rachel": "21m00Tcm4TlvDq8ikWAM",
    "adam": "pNInz6obpgDQGcFmaJgB",
    "antoni": "ErXwobaYiN019PkySvjV",
    "bella": "EXAVITQu4vr4xnSDxMaL",
    "elli": "MF3mGyEYCl7XYWbV9V6O",
    "josh": "TxGEqnHWrfWFTfGW9XjX",
    "sam": "yoZ06aMxZJJ28mfd3POQ",
    "domi": "AZnzlk1XvdvUeBnXmlld",
}


async def elevenlabs_tts(
    text: str,
    dest: str,
    api_key: str,
    voice_id: str = "21m00Tcm4TlvDq8ikWAM",
    model_id: str = "eleven_multilingual_v2",
    http_client=None,
    sem: Optional[asyncio.Semaphore] = None,
) -> str:
    url = f"{BASE}/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
    }
    body = {
        "text": text,
        "model_id": model_id,
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
        },
    }

    async def _do():
        if http_client and hasattr(http_client, 'post_raw'):
            return await http_client.post_raw(url, json=body, headers=headers)
        import aiohttp
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=body, headers=headers) as r:
                r.raise_for_status()
                return await r.read()

    for attempt in range(3):
        try:
            if sem:
                async with sem:
                    audio_bytes = await _do()
            else:
                audio_bytes = await _do()
            break
        except Exception as e:
            if attempt == 2:
                raise
            log.debug("elevenlabs tts retry %d: %s", attempt + 1, e)
            await asyncio.sleep(2.0 * (attempt + 1))

    with open(dest, "wb") as f:
        f.write(audio_bytes)
    return dest


async def generate_tts_elevenlabs(
    segments: list[dict],
    output_dir: str,
    api_key: str = "",
    api_keys: Optional[list[str]] = None,
    voice_id: str = "21m00Tcm4TlvDq8ikWAM",
    model_id: str = "eleven_multilingual_v2",
    concurrency: int = 10,
    http_client=None,
    cache=None,
) -> list[str]:
    keys = api_keys or ([api_key] if api_key else [])
    if not keys:
        raise ValueError("ElevenLabs requires at least one API key")

    key_pool = KeyPool(keys)
    sem = asyncio.Semaphore(concurrency)

    async def _gen(i: int, text: str) -> str:
        dest = os.path.join(output_dir, f"vo_{i:03d}.mp3")
        if cache:
            cache_key = f"el-tts:{voice_id}:{text}"
            cached = cache.get(cache_key)
            if cached:
                import shutil
                shutil.copy2(cached, dest)
                return dest

        key = await key_pool.get()
        try:
            await elevenlabs_tts(text, dest, key, voice_id, model_id,
                                 http_client, sem)
            await key_pool.report_success(key)
        except Exception as e:
            if "429" in str(e) or "Too Many" in str(e):
                await key_pool.report_error(key, cooldown=30.0)
            raise

        if cache:
            cache.put(f"el-tts:{voice_id}:{text}", dest)
        return dest

    tasks = [_gen(i, seg["vo"]) for i, seg in enumerate(segments)]
    paths = await asyncio.gather(*tasks)
    log.info("ElevenLabs TTS: generated %d segments (%d keys)",
             len(paths), key_pool.size)
    return list(paths)
