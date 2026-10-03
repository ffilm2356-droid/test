"""Main orchestrator — fully async pipeline.

Stages:
  1. TTS generation (parallel)
  2. Media search (parallel across all providers)
  3. Shot planning + media download + rendering (parallel)
  4. Final mux (sequential)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Optional

from .cache import MediaCache
from .cards import CARDS, lower_third, prepare_photo, render_card, card_text
from .composer import (
    concat_files, get_duration, init_composer, mux_final,
    pad_audio, render_still, render_video,
)
from .config import Config
from .providers.base import MediaItem, MediaProvider, SearchResult
from .providers.commons import CommonsProvider
from .providers.unsplash import UnsplashProvider
from .providers.pexels import PexelsProvider
from .providers.pixabay import PixabayProvider
from .proxy import HttpClient, ProxyPool
from .tts import generate_tts

log = logging.getLogger("mediaforge.pipeline")


@dataclass
class RenderResult:
    output_path: str
    credits_path: str
    duration: float
    shot_count: int
    credited_media: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class MediaPool:
    """Manages media items across multiple providers with dedup."""

    def __init__(self, providers: list[MediaProvider]):
        self.providers = providers
        self._pools: dict[tuple[str, str], list[MediaItem]] = {}
        self._used: set[str] = set()

    async def search_all(self, query: str, kind: str = "image") -> list[MediaItem]:
        key = (query, kind)
        if key in self._pools:
            return self._pools[key]

        tasks = [p.search(query, kind) for p in self.providers]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        items: list[MediaItem] = []
        seen_urls: set[str] = set()
        for r in results:
            if isinstance(r, Exception):
                log.debug("provider search error: %s", r)
                continue
            for item in r.items:
                if item.url not in seen_urls:
                    seen_urls.add(item.url)
                    items.append(item)

        if kind == "image":
            items.sort(key=lambda x: not x.is_landscape)

        self._pools[key] = items
        return items

    def take(self, query: str, kind: str = "image") -> Optional[MediaItem]:
        items = self._pools.get((query, kind), [])
        for item in items:
            if item.url not in self._used:
                self._used.add(item.url)
                return item
        return None


async def render_spec(
    spec: dict | str | Path,
    output_dir: str | Path,
    config: Optional[Config] = None,
) -> RenderResult:
    """Main entry point. Renders a full video from a JSON spec."""
    if isinstance(spec, (str, Path)):
        spec = json.loads(Path(spec).read_text())

    if config is None:
        config = Config.from_env()

    output_dir = str(output_dir)
    os.makedirs(output_dir, exist_ok=True)
    tmp = f"{output_dir}/tmp"
    os.makedirs(tmp, exist_ok=True)

    segs = spec["segments"]
    voice = spec.get("voice", config.tts_voice)
    rate = spec.get("rate", config.tts_rate)
    gap = spec.get("gap", 0.28)

    init_composer(config.max_workers)

    # --- build providers ---
    proxy_pool = ProxyPool(config.proxy)
    http = HttpClient(proxy_pool, config.proxy)
    cache = MediaCache(config.cache_dir)

    providers: list[MediaProvider] = []
    providers.append(CommonsProvider(config.wikimedia, http, cache))
    if config.unsplash.api_key and config.unsplash.enabled:
        providers.append(UnsplashProvider(config.unsplash, http, cache))
    if config.pexels.api_key and config.pexels.enabled:
        providers.append(PexelsProvider(config.pexels, http, cache))
    if config.pixabay.api_key and config.pixabay.enabled:
        providers.append(PixabayProvider(config.pixabay, http, cache))

    pool = MediaPool(providers)
    errors: list[str] = []

    # --- Stage 1: TTS ---
    log.info("[1/4] TTS %d segments", len(segs))
    tts_paths = await generate_tts(
        segs, tmp, voice, rate, config.tts_concurrency, cache,
    )

    # --- Stage 2: Search (parallel) ---
    queries: set[tuple[str, str]] = set()
    for s in segs:
        for v in s["v"]:
            if "q" in v:
                queries.add((v["q"], "image"))
            if "vq" in v:
                queries.add((v["vq"], "video"))
            for alt in v.get("alt", []):
                queries.add((alt, "image"))
    for fb in spec.get("fallback", []):
        queries.add((fb, "image"))

    search_tasks = [pool.search_all(q, k) for q, k in queries]
    await asyncio.gather(*search_tasks, return_exceptions=True)
    log.info("[2/4] searched %d queries across %d providers",
             len(queries), len(providers))

    # --- Stage 3: Plan shots + render ---
    rnd = random.Random(spec.get("seed", 7))
    motions = ["pr", "zin", "pl", "zout"]
    fb_i = 0

    shots: list[dict] = []
    seg_frames: list[int] = []
    loop = asyncio.get_event_loop()

    for si, s in enumerate(segs):
        vo_dur = await get_duration(tts_paths[si])
        total = vo_dur + gap
        items = list(s["v"])
        media_items = [v for v in items if "card" not in v]
        maxshot = s.get("maxshot", spec.get("maxshot", 4.2))

        while total / len(items) > maxshot and media_items:
            items.append(media_items[(len(items) - len(s["v"])) % len(media_items)])

        weights = [1.5 if "card" in v else 1.0 for v in items]
        tf = round(total * 30)
        fr = [max(int(tf * w / sum(weights)), 12) for w in weights]
        fr[-1] = tf - sum(fr[:-1])
        seg_frames.append(tf)

        for v, f in zip(items, fr):
            shot = dict(seg=si, frames=f, spec=v, idx=len(shots),
                        motion=motions[len(shots) % 4])

            if "card" not in v:
                it = None
                if "vq" in v:
                    it = pool.take(v["vq"], "video")
                if not it and "q" in v:
                    it = pool.take(v["q"], "image")
                for alt_q in v.get("alt", []):
                    if it:
                        break
                    it = pool.take(alt_q, "image")
                tries = 0
                while not it and spec.get("fallback") and tries < len(spec["fallback"]):
                    it = pool.take(
                        spec["fallback"][fb_i % len(spec["fallback"])], "image")
                    fb_i += 1
                    tries += 1
                shot["media"] = it
            shots.append(shot)

    log.info("[3/4] planned %d shots, %.1fs total",
             len(shots), sum(seg_frames) / 30)

    used_media: list[MediaItem] = []

    async def do_shot(sh: dict) -> Optional[MediaItem]:
        i, f, v = sh["idx"], sh["frames"], sh["spec"]
        outp = f"{tmp}/shot_{i:04d}.mp4"
        if os.path.exists(outp):
            return None

        ov = None
        if v.get("lower"):
            ov = f"{tmp}/lt_{i:04d}.png"
            await loop.run_in_executor(None, lower_third, v["lower"], ov)

        if "card" in v:
            p = f"{tmp}/card_{i:04d}.png"
            card_img = await loop.run_in_executor(None, render_card, v["card"])
            await loop.run_in_executor(None, card_img.save, p)
            await render_still(p, f, outp, "zin", ov)
            return None

        it = sh.get("media")
        if it and it.kind == "video":
            try:
                await render_video(it.url, f, outp, i, v.get("bw", False), ov)
                return it
            except Exception as e:
                log.debug("video shot fail -> still: %s", e)
                it = pool.take(
                    v.get("q") or (spec.get("fallback") or ["city"])[0], "image")

        if it:
            raw = f"{tmp}/raw_{i:04d}"
            try:
                for prov in providers:
                    try:
                        await prov.download(it, raw)
                        break
                    except Exception:
                        continue
                p = f"{tmp}/img_{i:04d}.jpg"
                await loop.run_in_executor(
                    None, prepare_photo, raw, p, v.get("bw", False))
                await render_still(p, f, outp, sh["motion"], ov)
                return it
            except Exception as e:
                log.debug("img shot fail: %s", e)

        p = f"{tmp}/card_{i:04d}.png"
        text = (v.get("q") or "").upper()[:28] or "..."
        fallback_card = await loop.run_in_executor(
            None, card_text, {"text": text})
        await loop.run_in_executor(None, fallback_card.save, p)
        await render_still(p, f, outp, "zin", ov)
        return None

    shot_results = await asyncio.gather(
        *[do_shot(sh) for sh in shots], return_exceptions=True,
    )
    for r in shot_results:
        if isinstance(r, Exception):
            errors.append(str(r))
        elif r is not None:
            used_media.append(r)

    # --- Stage 4: Mux ---
    log.info("[4/4] mux")

    audio_tasks = []
    for si, tf in enumerate(seg_frames):
        wav = f"{tmp}/seg_{si:03d}.wav"
        audio_tasks.append(pad_audio(tts_paths[si], wav, tf / 30))
    await asyncio.gather(*audio_tasks)

    with open(f"{tmp}/alist.txt", "w") as fa:
        for si in range(len(seg_frames)):
            fa.write(f"file '{os.path.abspath(tmp)}/seg_{si:03d}.wav'\n")

    with open(f"{tmp}/vlist.txt", "w") as fv:
        for sh in shots:
            fv.write(f"file '{os.path.abspath(tmp)}/shot_{sh['idx']:04d}.mp4'\n")

    await concat_files(f"{tmp}/vlist.txt", f"{tmp}/video.mp4")
    await concat_files(f"{tmp}/alist.txt", f"{tmp}/voice.wav")

    final = f"{output_dir}/{spec['id']}.mp4"
    await mux_final(
        f"{tmp}/video.mp4", f"{tmp}/voice.wav", final,
        spec.get("music"), spec.get("music_volume", 0.10),
    )

    # --- Credits ---
    seen: set[str] = set()
    credit_lines: list[str] = []
    credit_dicts: list[dict] = []
    for it in used_media:
        if it.page_url in seen:
            continue
        seen.add(it.page_url)
        credit_lines.append(
            f"- {it.title} -- {it.artist or 'unknown'} -- "
            f"{it.license} -- {it.page_url}"
        )
        credit_dicts.append({
            "title": it.title, "artist": it.artist,
            "license": it.license, "page_url": it.page_url,
            "provider": it.provider,
        })

    credits_path = f"{output_dir}/{spec['id']}-credits.txt"
    with open(credits_path, "w") as f:
        f.write(
            f"{spec.get('title', spec['id'])}\n"
            f"Visuals: multi-provider (licenses below). "
            f"Voice: Microsoft neural TTS ({voice}).\n\n"
            + "\n".join(credit_lines) + "\n"
        )

    duration = await get_duration(final)
    log.info("DONE %s %.1fs, %d credited files", final, duration, len(credit_lines))

    await http.close()

    return RenderResult(
        output_path=final,
        credits_path=credits_path,
        duration=duration,
        shot_count=len(shots),
        credited_media=credit_dicts,
        errors=errors,
    )
