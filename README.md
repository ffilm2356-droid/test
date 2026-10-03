# MediaForge

API-first media generation pipeline. No browser, no captcha, no headless Chrome.

## Features

- **Pure API** — all media fetched via REST APIs (Wikimedia Commons, Unsplash, Pexels, Pixabay)
- **10x faster** — async I/O, parallel downloads, concurrent FFmpeg rendering
- **VPN-to-proxy** — built-in SOCKS5/HTTP proxy rotation, use your VPN as a proxy
- **Content cache** — SHA256-keyed dedup, skip re-downloads across runs
- **Rate limiting** — per-provider token-bucket, no bans
- **Server-ready** — importable module + CLI, no GUI dependencies

## Quick Start

```bash
pip install -e .

# minimal (Wikimedia Commons only, no API key needed)
python -m src spec.json output/

# with API keys for more sources
export UNSPLASH_ACCESS_KEY=your_key
export PEXELS_API_KEY=your_key
export PIXABAY_API_KEY=your_key
python -m src spec.json output/

# with proxy rotation
python -m src spec.json output/ \
  -p socks5://127.0.0.1:1080 \
  -p http://proxy2:8080

# with VPN gateway
python -m src spec.json output/ \
  --vpn-gateway 10.8.0.1 \
  --vpn-interface tun0
```

## Config

Copy `config.example.json` and fill in API keys:

```bash
cp config.example.json config.json
python -m src spec.json output/ -c config.json
```

Environment variables:

| Variable | Description |
|----------|-------------|
| `UNSPLASH_ACCESS_KEY` | Unsplash API key |
| `PEXELS_API_KEY` | Pexels API key |
| `PIXABAY_API_KEY` | Pixabay API key |
| `MEDIAFORGE_PROXIES` | Comma-separated proxy URLs |
| `MEDIAFORGE_VPN_GATEWAY` | VPN gateway address |
| `MEDIAFORGE_VPN_INTERFACE` | VPN interface (default: tun0) |
| `MEDIAFORGE_CACHE_DIR` | Cache directory path |
| `MEDIAFORGE_WORKERS` | Parallel FFmpeg workers |

## Spec Format

```json
{
  "id": "video-01",
  "title": "Video Title",
  "voice": "en-US-ChristopherNeural",
  "rate": "+6%",
  "gap": 0.28,
  "seed": 7,
  "maxshot": 4.2,
  "workers": 6,
  "music": "/path/to/bgm.mp3",
  "fallback": ["city skyline", "technology"],
  "segments": [
    {
      "vo": "Voiceover text for this segment.",
      "v": [
        {"q": "search query", "lower": "Lower third text"},
        {"vq": "video search query"},
        {"card": {"t": "big", "text": "$4,000", "sub": "Subtitle"}}
      ]
    }
  ]
}
```

### Card Types

| Type | Fields |
|------|--------|
| `big` | `text`, `sub`, `color` |
| `bars` | `title`, `items` (array of [label, value, display]), `hl` |
| `pct` | `title`, `pct`, `label` |
| `timeline` | `title`, `points` (array of [year, label]), `hl` |
| `boxes` | `parent`, `items`, `hl`, `caption` |
| `doc` | `head`, `body`, `stamp` |
| `text` | `text`, `sub`, `red` |
| `vs` | `left`, `right` (each [label, value]), `hl` |

## Architecture

```
src/
  __init__.py          # package root
  config.py            # API keys, proxy config, rate limits
  proxy.py             # proxy rotation, VPN gateway, SOCKS5
  cache.py             # content-addressable cache
  rate_limiter.py      # token-bucket rate limiter
  providers/
    base.py            # abstract provider + MediaItem type
    commons.py         # Wikimedia Commons (free, no key)
    unsplash.py        # Unsplash API
    pexels.py          # Pexels API (photos + videos)
    pixabay.py         # Pixabay API (photos + videos)
  tts.py               # async TTS via edge-tts
  cards.py             # Pillow-based data cards (8 types)
  composer.py          # async FFmpeg compositing
  pipeline.py          # main orchestrator
  cli.py               # CLI entry point
```

## As a Module

```python
import asyncio
from src.pipeline import render_spec
from src.config import Config

config = Config.from_env()
result = asyncio.run(render_spec("spec.json", "output/", config))
print(result.output_path, result.duration)
```

## Requirements

- Python 3.10+
- FFmpeg + ffprobe
- System fonts (DejaVu or Montserrat recommended)
