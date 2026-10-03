# MediaForge

API-first media generation pipeline. No browser, no captcha, no headless Chrome.

## Features

- **Pure API** — all media fetched via REST APIs (Wikimedia Commons, Unsplash, Pexels, Pixabay)
- **Google AI Studio** — Imagen 3 (image gen), Veo 2 (video gen), Gemini TTS, Gemini chat/agent
- **Bypass BotGuard** — direct REST API calls, no browser, no captcha, no headless Chrome
- **10x faster** — async I/O, parallel downloads, concurrent FFmpeg rendering
- **100 images in 2min** — 50 concurrent Imagen requests, cached results
- **VPN-to-proxy** — built-in SOCKS5/HTTP proxy rotation, use your VPN as a proxy
- **Content cache** — SHA256-keyed dedup, skip re-downloads/re-generations across runs
- **Rate limiting** — per-provider token-bucket, no bans
- **Server-ready** — importable module + CLI, no GUI dependencies

## Quick Start

```bash
pip install -e .

# with Google AI Studio (recommended)
export GOOGLE_AI_API_KEY=your_key
python -m src spec.json output/

# minimal (Wikimedia Commons only, no API key needed)
python -m src spec.json output/

# with all API keys
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

## Google AI Studio Integration

Get your API key from https://aistudio.google.com/apikey

### Image Generation (Imagen 3)

```python
import asyncio
from src.providers.google_ai import GoogleAIProvider, batch_generate_images
from src.config import Config

config = Config.from_env()
# ... setup http, cache ...

provider = GoogleAIProvider(config.google_ai, http, cache, concurrency=50)

# single image
await provider.generate_image_to_file("sunset over mountains", "out.jpg")

# batch: 100 images in ~2 minutes
prompts = ["prompt 1", "prompt 2", ...]
paths = await batch_generate_images(provider, prompts, "output/")
```

### Video Generation (Veo 2)

```python
from src.providers.google_ai import batch_generate_videos

# with reference image
videos = await batch_generate_videos(provider, [
    {"prompt": "cinematic city flyover", "duration": 5},
    {"prompt": "product rotation", "ref_image": "ref.jpg", "duration": 8},
], "output/")
```

### Gemini TTS

```python
from src.gemini_tts import gemini_tts, generate_tts_gemini

# single
await gemini_tts("Hello world", "out.wav", api_key, voice="Kore")

# batch for all segments
paths = await generate_tts_gemini(segments, "tmp/", api_key, voice="Kore", concurrency=20)
```

Available voices: Zephyr, Puck, Charon, Kore, Fenrir, Leda, Orus, Aoede, Callirrhoe, Autonoe

### Gemini Chat/Agent

```python
from src.gemini import GeminiClient

client = GeminiClient(api_key, model="gemini-2.0-flash")

# text generation
response = await client.generate("Write a video script about AI")

# with image input
response = await client.generate_with_image("Describe this", "photo.jpg")

# batch
responses = await client.batch_generate(["prompt1", "prompt2"])

# JSON mode
data = await client.generate("List 5 facts", json_mode=True)
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
| `GOOGLE_AI_API_KEY` | Google AI Studio API key (Imagen + Veo + Gemini TTS) |
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
  "tts": "gemini",
  "gemini_voice": "Kore",
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

Set `"tts": "gemini"` to use Gemini TTS (auto when GOOGLE_AI_API_KEY is set).
Set `"tts": "edge"` to force edge-tts.

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
  gemini.py            # Gemini chat/agent client (REST API)
  gemini_tts.py        # Gemini TTS (REST API)
  providers/
    base.py            # abstract provider + MediaItem type
    commons.py         # Wikimedia Commons (free, no key)
    unsplash.py        # Unsplash API
    pexels.py          # Pexels API (photos + videos)
    pixabay.py         # Pixabay API (photos + videos)
    google_ai.py       # Google AI Studio (Imagen 3 + Veo 2)
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

### Standalone Google AI Usage

```python
import asyncio
from src.providers.google_ai import GoogleAIProvider, batch_generate_images
from src.gemini import GeminiClient
from src.gemini_tts import generate_tts_gemini
from src.config import Config
from src.proxy import ProxyPool, HttpClient
from src.cache import MediaCache

config = Config.from_env()
pool = ProxyPool(config.proxy)
http = HttpClient(pool, config.proxy)
cache = MediaCache(config.cache_dir)

# image gen
provider = GoogleAIProvider(config.google_ai, http, cache)
paths = asyncio.run(batch_generate_images(provider, ["cat on beach", "city at night"], "out/"))

# chat
client = GeminiClient(config.google_ai.api_key, http_client=http)
answer = asyncio.run(client.generate("Hello"))

# tts
asyncio.run(generate_tts_gemini(
    [{"vo": "Hello world"}], "out/", config.google_ai.api_key, http_client=http))
```

## Requirements

- Python 3.10+
- FFmpeg + ffprobe
- System fonts (DejaVu or Montserrat recommended)
