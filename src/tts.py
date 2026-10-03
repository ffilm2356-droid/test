"""Async TTS generation via edge-tts with caching."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

from .cache import MediaCache

log = logging.getLogger("mediaforge.tts")


async def get_duration(path: str) -> float:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "csv=p=0", path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    return float(stdout.decode().strip() or 0)


async def generate_tts(
    segments: list[dict],
    output_dir: str,
    voice: str = "en-US-ChristopherNeural",
    rate: str = "+6%",
    concurrency: int = 6,
    cache: Optional[MediaCache] = None,
) -> list[str]:
    """Generate TTS audio for all segments. Returns list of MP3 paths."""
    import edge_tts
    import hashlib

    os.makedirs(output_dir, exist_ok=True)
    sem = asyncio.Semaphore(concurrency)
    paths: list[str] = [f"{output_dir}/vo_{i:03d}.mp3" for i in range(len(segments))]

    async def gen_one(i: int, text: str):
        p = paths[i]

        if cache:
            cache_key = f"tts:{hashlib.sha256(f'{voice}:{rate}:{text}'.encode()).hexdigest()}"
            cached = cache.get(cache_key)
            if cached:
                import shutil
                shutil.copy2(cached, p)
                return

        if os.path.exists(p) and os.path.getsize(p) > 1000:
            return

        async with sem:
            for attempt in range(4):
                try:
                    await edge_tts.Communicate(text, voice, rate=rate).save(p)
                    if cache:
                        cache.put(cache_key, p, {"voice": voice, "rate": rate})
                    return
                except Exception as e:
                    log.warning("tts retry seg %d attempt %d: %s", i, attempt, e)
                    await asyncio.sleep(2 + attempt * 3)
            raise RuntimeError(f"TTS failed for segment {i} after 4 attempts")

    await asyncio.gather(*[gen_one(i, s["vo"]) for i, s in enumerate(segments)])
    return paths
