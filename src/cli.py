"""CLI entry point — python -m src spec.json output_dir [options]"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from .config import Config
from .pipeline import render_spec


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="mediaforge",
        description="API-first media generation pipeline. No browser, no captcha.",
    )
    p.add_argument("spec", help="JSON spec file path")
    p.add_argument("output_dir", help="Output directory")
    p.add_argument("-c", "--config", help="Config JSON file path")
    p.add_argument("-p", "--proxy", action="append", default=[],
                   help="Proxy URL (socks5://..., http://...). Repeatable.")
    p.add_argument("--vpn-gateway", help="VPN gateway address (auto SOCKS5 proxy)")
    p.add_argument("--vpn-interface", default="tun0", help="VPN interface name")
    p.add_argument("--cache-dir", help="Cache directory path")
    p.add_argument("--no-cache", action="store_true", help="Disable caching")
    p.add_argument("-w", "--workers", type=int, help="Parallel FFmpeg workers")
    p.add_argument("--unsplash-key", help="Unsplash API key")
    p.add_argument("--pexels-key", help="Pexels API key")
    p.add_argument("--pixabay-key", help="Pixabay API key")
    p.add_argument("--google-ai-key", action="append", default=[],
                   help="Google AI Studio API key (repeatable for multi-key rotation)")
    p.add_argument("--gemini-tts-voice", help="Gemini TTS voice (default: Kore)")
    p.add_argument("--elevenlabs-key", action="append", default=[],
                   help="ElevenLabs API key (repeatable for multi-key rotation)")
    p.add_argument("--elevenlabs-voice", help="ElevenLabs voice ID")
    p.add_argument("--tts-engine", choices=["auto", "gemini", "elevenlabs", "edge"],
                   help="TTS engine to use")
    p.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    p.add_argument("--dry-run", action="store_true",
                   help="Plan shots and report without rendering")
    p.add_argument("--width", type=int, help="Video width (default 1280)")
    p.add_argument("--height", type=int, help="Video height (default 720)")
    return p.parse_args()


def main():
    args = parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    config_dict: dict = {}
    if args.config:
        with open(args.config) as f:
            config_dict = json.load(f)

    if args.proxy:
        config_dict.setdefault("proxy", {})
        config_dict["proxy"]["enabled"] = True
        config_dict["proxy"]["proxies"] = args.proxy

    if args.vpn_gateway:
        config_dict.setdefault("proxy", {})
        config_dict["proxy"]["enabled"] = True
        config_dict["proxy"]["vpn_gateway"] = args.vpn_gateway
        config_dict["proxy"]["vpn_interface"] = args.vpn_interface

    if args.unsplash_key:
        config_dict.setdefault("unsplash", {})["api_key"] = args.unsplash_key
    if args.pexels_key:
        config_dict.setdefault("pexels", {})["api_key"] = args.pexels_key
    if args.pixabay_key:
        config_dict.setdefault("pixabay", {})["api_key"] = args.pixabay_key
    if args.google_ai_key:
        gai = config_dict.setdefault("google_ai", {})
        gai["api_key"] = args.google_ai_key[0]
        if len(args.google_ai_key) > 1:
            gai["api_keys"] = args.google_ai_key
    if args.gemini_tts_voice:
        config_dict["gemini_tts_voice"] = args.gemini_tts_voice
    if args.elevenlabs_key:
        el = config_dict.setdefault("elevenlabs", {})
        el["api_key"] = args.elevenlabs_key[0]
        if len(args.elevenlabs_key) > 1:
            el["api_keys"] = args.elevenlabs_key
    if args.elevenlabs_voice:
        config_dict.setdefault("elevenlabs", {})["voice_id"] = args.elevenlabs_voice
    if args.workers:
        config_dict["max_workers"] = args.workers
    if args.width:
        config_dict["width"] = args.width
    if args.height:
        config_dict["height"] = args.height

    config = Config.from_dict(config_dict)

    if args.cache_dir:
        from pathlib import Path
        config.cache_dir = Path(args.cache_dir)

    with open(args.spec) as f:
        spec = json.load(f)

    if args.dry_run:
        print(f"Spec: {spec.get('id', 'unknown')}")
        print(f"Segments: {len(spec['segments'])}")
        total_shots = sum(len(s['v']) for s in spec['segments'])
        print(f"Planned shots: {total_shots}")
        queries = set()
        for s in spec['segments']:
            for v in s['v']:
                if 'q' in v:
                    queries.add(v['q'])
                if 'vq' in v:
                    queries.add(v['vq'])
        print(f"Unique queries: {len(queries)}")
        gai_keys = len(config.google_ai.api_keys) or (1 if config.google_ai.api_key else 0)
        el_keys = len(config.elevenlabs.api_keys) or (1 if config.elevenlabs.api_key else 0)
        print(f"Providers: wikimedia" +
              (", unsplash" if config.unsplash.api_key else "") +
              (", pexels" if config.pexels.api_key else "") +
              (", pixabay" if config.pixabay.api_key else "") +
              (f", google_ai ({gai_keys} keys)" if config.google_ai.api_key else ""))
        tts_name = "elevenlabs" if el_keys else ("gemini" if gai_keys else "edge-tts")
        print(f"TTS: {tts_name}" +
              (f" ({el_keys} keys)" if el_keys else
               f" (voice={config.gemini_tts_voice})" if gai_keys else
               f" (voice={config.tts_voice})"))
        print(f"Proxy: {'enabled' if config.proxy.enabled else 'disabled'}" +
              (f" ({len(config.proxy.proxies)} proxies)" if config.proxy.proxies else ""))
        return

    result = asyncio.run(render_spec(spec, args.output_dir, config))

    print(f"\nDONE {result.output_path}")
    print(f"  Duration: {result.duration:.1f}s")
    print(f"  Shots: {result.shot_count}")
    print(f"  Credits: {len(result.credited_media)} files")
    if result.errors:
        print(f"  Warnings: {len(result.errors)}")
        for e in result.errors[:5]:
            print(f"    - {e[:200]}")


if __name__ == "__main__":
    main()
