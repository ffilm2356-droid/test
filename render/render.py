#!/usr/bin/env python3
"""Render a faceless documentary video from a JSON spec using free media.

Visuals: Wikimedia Commons photos/videos (CC / public domain) with Ken Burns
motion, plus generated data cards (black grid + red bars, white grid timeline,
legal document) in the competitor's visual language.
Voice: edge-tts (Microsoft neural voices, free).

Usage: python3 render.py spec.json out_dir
"""
import asyncio
import concurrent.futures as cf
import hashlib
import html
import json
import math
import os
import random
import re
import subprocess
import sys
import urllib.parse
import urllib.request

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

W, H, FPS = 1280, 720, 30
OVER = 1.14  # oversize factor for Ken Burns headroom
UA = {"User-Agent": "DocRenderBot/1.0 (https://github.com/ffilm2356-droid/test)"}
BLACKLIST = re.compile(
    r"propaganda|\bwar\b|\bAI\b|gameplay|star wars|harry potter|logo|icon|map of|diagram|"
    r"chart|graph|svg|pdf|sign language|captation|nude|weapon|protest",
    re.I,
)
RED, WHITE, BLACK = (226, 35, 26), (245, 245, 245), (8, 8, 10)
YELLOW, GREY = (245, 197, 24), (120, 120, 128)

FONT_DIRS = ["/usr/share/fonts", "/usr/local/share/fonts", os.path.expanduser("~/.fonts"),
             os.path.expanduser("~/.local/share/fonts")]


def find_font(names):
    for d in FONT_DIRS:
        for root, _, files in os.walk(d):
            for f in files:
                for n in names:
                    if f.lower() == n.lower():
                        return os.path.join(root, f)
    return None


F_BLACK = find_font(["Montserrat-Black.ttf", "Montserrat-ExtraBold.ttf", "Montserrat-Bold.ttf",
                     "Metropolis-Black.otf", "Metropolis-Bold.otf", "DejaVuSans-Bold.ttf"])
F_BOLD = find_font(["Montserrat-Bold.ttf", "Metropolis-Bold.otf", "DejaVuSans-Bold.ttf"]) or F_BLACK
F_REG = find_font(["Montserrat-Medium.ttf", "Montserrat-Regular.ttf", "Metropolis-Medium.otf",
                   "DejaVuSans.ttf"]) or F_BOLD
F_SERIF = find_font(["DejaVuSerif-Bold.ttf", "LiberationSerif-Bold.ttf"]) or F_BOLD


def font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def sh(cmd):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"cmd failed: {cmd[:300]}\n{r.stderr[-1500:]}")
    return r.stdout


def http_get(url, dest=None, timeout=60):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    if dest:
        with open(dest, "wb") as f:
            f.write(data)
    return data


# ---------------------------------------------------------------- Commons
class Commons:
    def __init__(self, cache):
        self.cache = cache
        self.used = set()
        self.pools = {}
        self.credits = []

    def api(self, params):
        u = "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(params)
        return json.loads(http_get(u, timeout=40))

    def search(self, q, kind):
        key = (q, kind)
        if key in self.pools:
            return self.pools[key]
        ft = "filetype:video" if kind == "v" else "filetype:bitmap"
        params = dict(action="query", format="json", generator="search",
                      gsrsearch=f"{q} {ft}", gsrnamespace=6, gsrlimit=30)
        if kind == "v":
            params.update(prop="videoinfo", viprop="url|size|mime|extmetadata|derivatives")
        else:
            params.update(prop="imageinfo", iiprop="url|size|mime|extmetadata", iiurlwidth=1920)
        try:
            d = self.api(params)
        except Exception as e:
            print("search fail", q, e, flush=True)
            d = {}
        pages = sorted((d.get("query", {}) or {}).get("pages", {}).values(), key=lambda p: p.get("index", 0))
        out = []
        for p in pages:
            info = (p.get("videoinfo") or p.get("imageinfo") or [None])[0]
            if not info:
                continue
            title = p["title"][5:]
            if BLACKLIST.search(title):
                continue
            meta = info.get("extmetadata", {})
            lic = meta.get("LicenseShortName", {}).get("value", "")
            if not lic or "fair use" in lic.lower():
                continue
            artist = re.sub("<[^>]+>", "", html.unescape(meta.get("Artist", {}).get("value", ""))).strip()
            w, h = info.get("width", 0), info.get("height", 0)
            item = dict(title=title, license=lic, artist=artist[:120],
                        page=info.get("descriptionurl", ""), w=w, h=h, kind=kind)
            if kind == "v":
                ders = [x for x in info.get("derivatives", []) if "webm" in x.get("type", "") or "mp4" in x.get("type", "")]
                ders = [x for x in ders if 360 <= int(x.get("height", 0) or 0) <= 1080]
                if not ders or w < 640 or (w / max(h, 1)) < 1.3:
                    continue
                ders.sort(key=lambda x: abs(int(x.get("height", 0)) - 720))
                item["url"] = ders[0]["src"]
            else:
                if w < 900 or info.get("mime") not in ("image/jpeg", "image/png", "image/webp"):
                    continue
                item["url"] = info.get("thumburl") or info["url"]
                item["landscape"] = (w / max(h, 1)) >= 1.25
            out.append(item)
        if kind == "i":  # landscape first
            out.sort(key=lambda x: not x["landscape"])
        self.pools[key] = out
        return out

    def take(self, q, kind="i"):
        for it in self.search(q, kind):
            if it["url"] not in self.used:
                self.used.add(it["url"])
                return it
        return None


# ---------------------------------------------------------------- cards
def grid_bg(dark=True):
    w, h = int(W * OVER), int(H * OVER)
    img = Image.new("RGB", (w, h), BLACK if dark else (238, 238, 236))
    d = ImageDraw.Draw(img)
    c = (34, 34, 40) if dark else (210, 210, 214)
    for x in range(0, w, 48):
        d.line([(x, 0), (x, h)], fill=c, width=1)
    for y in range(0, h, 48):
        d.line([(0, y), (w, y)], fill=c, width=1)
    return img, d


def fit_text(d, text, path, max_w, start):
    s = start
    while s > 14:
        f = font(path, s)
        if d.textlength(text, font=f) <= max_w:
            return f
        s -= 4
    return font(path, 14)


def center_text(d, y, text, f, fill, w):
    tw = d.textlength(text, font=f)
    d.text(((w - tw) / 2, y), text, font=f, fill=fill)


def card_big(c):
    img, d = grid_bg(True)
    w, h = img.size
    f = fit_text(d, c["text"], F_BLACK, w - 220, 230)
    bb = d.textbbox((0, 0), c["text"], font=f)
    th = bb[3] - bb[1]
    y = h / 2 - th / 2 - 50 - bb[1]
    center_text(d, y, c["text"], f, c.get("color") and tuple(c["color"]) or WHITE, w)
    d.rectangle([w / 2 - 170, y + bb[3] + 30, w / 2 + 170, y + bb[3] + 44], fill=RED)
    if c.get("sub"):
        fs = fit_text(d, c["sub"].upper(), F_BOLD, w - 260, 52)
        center_text(d, y + bb[3] + 70, c["sub"].upper(), fs, WHITE, w)
    return img


def card_bars(c):
    img, d = grid_bg(True)
    w, h = img.size
    items = c["items"]
    vmax = max(i[1] for i in items) or 1
    ft = fit_text(d, c.get("title", "").upper(), F_BOLD, w - 240, 48)
    d.text((140, 120), c.get("title", "").upper(), font=ft, fill=WHITE)
    n = len(items)
    top, bot = 230, h - 130
    bh = min(110, (bot - top) / n * 0.62)
    gap = (bot - top - bh * n) / max(n, 1)
    fl = font(F_BOLD, 34)
    fv = font(F_BLACK, int(bh * 0.62))
    lab_w = max(d.textlength(i[0].upper(), font=fl) for i in items) + 40
    x0 = 140 + lab_w
    maxlen = w - x0 - 260
    for k, it in enumerate(items):
        y = top + k * (bh + gap) + gap / 2
        d.text((140, y + bh / 2 - 20), it[0].upper(), font=fl, fill=WHITE)
        L = max(8, maxlen * it[1] / vmax)
        col = RED if k == c.get("hl", 0) else GREY
        d.rectangle([x0, y, x0 + L, y + bh], fill=col)
        d.text((x0 + L + 22, y + bh / 2 - fv.size / 1.6), it[2], font=fv, fill=WHITE)
    return img


def card_pct(c):
    img, d = grid_bg(True)
    w, h = img.size
    ft = fit_text(d, c["title"].upper(), F_BOLD, w - 280, 54)
    center_text(d, 210, c["title"].upper(), ft, WHITE, w)
    x0, x1, y0, y1 = 170, w - 170, 330, 450
    d.rectangle([x0, y0, x1, y1], outline=WHITE, width=4)
    d.rectangle([x0 + 8, y0 + 8, x0 + 8 + (x1 - x0 - 16) * c["pct"] / 100, y1 - 8], fill=RED)
    center_text(d, 490, c.get("label", f"{c['pct']}%"), font(F_BLACK, 110), WHITE, w)
    return img


def card_timeline(c):
    img, d = grid_bg(False)
    w, h = img.size
    ft = fit_text(d, c.get("title", "").upper(), F_BOLD, w - 240, 50)
    center_text(d, 130, c.get("title", "").upper(), ft, (20, 20, 24), w)
    pts = c["points"]
    y = h / 2 + 20
    x0, x1 = 170, w - 170
    d.line([(x0, y), (x1, y)], fill=(30, 30, 34), width=6)
    n = len(pts)
    fy = font(F_BLACK, 46)
    fl = font(F_BOLD, 26)
    for k, (yr, lab) in enumerate(pts):
        x = x0 + (x1 - x0) * (k / (n - 1) if n > 1 else 0.5)
        hl = k == c.get("hl", n - 1)
        r = 26 if hl else 16
        d.polygon([(x, y - r), (x + r, y), (x, y + r), (x - r, y)], fill=RED if hl else (30, 30, 34))
        tw = d.textlength(yr, font=fy)
        d.text((x - tw / 2, y - 110), yr, font=fy, fill=(20, 20, 24))
        words, lines, cur = lab.upper().split(), [], ""
        for wd in words:
            if d.textlength((cur + " " + wd).strip(), font=fl) > (x1 - x0) / max(n, 2) - 10:
                lines.append(cur)
                cur = wd
            else:
                cur = (cur + " " + wd).strip()
        lines.append(cur)
        for j, ln in enumerate(lines):
            lw = d.textlength(ln, font=fl)
            d.text((x - lw / 2, y + 50 + j * 34), ln, font=fl, fill=RED if hl else (40, 40, 46))
    return img


def card_boxes(c):
    img, d = grid_bg(True)
    w, h = img.size
    if c.get("parent"):
        fp = font(F_BLACK, 70)
        center_text(d, 120, c["parent"].upper(), fp, WHITE, w)
        d.rectangle([w / 2 - 120, 215, w / 2 + 120, 225], fill=RED)
    items = c["items"]
    cols = min(4, len(items)) if len(items) != 5 else 5
    rows = math.ceil(len(items) / cols)
    bw, bh = (w - 260) / cols - 24, 110
    fb = font(F_BOLD, 34)
    top = 290 if c.get("parent") else (h - rows * (bh + 30)) / 2
    for k, it in enumerate(items):
        r_, c_ = divmod(k, cols)
        x = 130 + c_ * (bw + 24)
        y = top + r_ * (bh + 30)
        hl = k in c.get("hl", [])
        d.rectangle([x, y, x + bw, y + bh], outline=RED if hl else WHITE, width=4, fill=(60, 12, 10) if hl else None)
        f2 = fit_text(d, it.upper(), F_BOLD, bw - 24, 34)
        tw = d.textlength(it.upper(), font=f2)
        d.text((x + (bw - tw) / 2, y + bh / 2 - f2.size / 1.6), it.upper(), font=f2, fill=WHITE)
    if c.get("caption"):
        fc = fit_text(d, c["caption"].upper(), F_BOLD, w - 260, 40)
        center_text(d, h - 150, c["caption"].upper(), fc, RED, w)
    return img


def card_doc(c):
    w, h = int(W * OVER), int(H * OVER)
    img = Image.new("RGB", (w, h), (28, 28, 30))
    d = ImageDraw.Draw(img)
    px0, px1 = w * 0.2, w * 0.8
    d.rectangle([px0 + 14, 70 + 14, px1 + 14, h + 40], fill=(10, 10, 12))
    d.rectangle([px0, 70, px1, h + 40], fill=(248, 246, 240))
    fh = fit_text(d, c["head"].upper(), F_SERIF, px1 - px0 - 80, 34)
    tw = d.textlength(c["head"].upper(), font=fh)
    d.text(((w - tw) / 2, 130), c["head"].upper(), font=fh, fill=(15, 15, 15))
    d.line([(px0 + 60, 190), (px1 - 60, 190)], fill=(15, 15, 15), width=2)
    fb = font(F_SERIF, 30)
    y = 230
    for para in c["body"].split("\n"):
        words, cur = para.split(), ""
        for wd in words:
            if d.textlength((cur + " " + wd).strip(), font=fb) > px1 - px0 - 140:
                d.text((px0 + 70, y), cur, font=fb, fill=(25, 25, 25))
                y += 44
                cur = wd
            else:
                cur = (cur + " " + wd).strip()
        d.text((px0 + 70, y), cur, font=fb, fill=(25, 25, 25))
        y += 62
    for k in range(10):
        yy = y + k * 34
        if yy > h - 20:
            break
        d.line([(px0 + 70, yy), (px1 - 70 - (k % 3) * 60, yy)], fill=(190, 190, 186), width=8)
    if c.get("stamp"):
        fs = font(F_BLACK, 64)
        st = Image.new("RGBA", (int(d.textlength(c["stamp"], font=fs)) + 60, 110), (0, 0, 0, 0))
        sd = ImageDraw.Draw(st)
        sd.rectangle([4, 4, st.width - 4, 106], outline=RED + (255,), width=7)
        sd.text((30, 14), c["stamp"], font=fs, fill=RED + (255,))
        st = st.rotate(12, expand=True)
        img.paste(st, (int(px1 - st.width - 20), int(h * 0.55)), st)
    return img


def card_text(c):
    img, d = grid_bg(True)
    w, h = img.size
    lines = c["text"].split("\n")
    fs = [fit_text(d, ln, F_BLACK, w - 260, 120 if len(lines) == 1 else 92) for ln in lines]
    total = sum(f.size * 1.2 for f in fs)
    y = (h - total) / 2 - 20
    for k, (ln, f) in enumerate(zip(lines, fs)):
        col = RED if k in c.get("red", []) else WHITE
        center_text(d, y, ln, f, col, w)
        y += f.size * 1.2
    if c.get("sub"):
        fsb = fit_text(d, c["sub"].upper(), F_BOLD, w - 300, 40)
        center_text(d, y + 30, c["sub"].upper(), fsb, GREY, w)
    return img


def card_vs(c):
    img, d = grid_bg(True)
    w, h = img.size
    d.rectangle([w / 2 - 3, 110, w / 2 + 3, h - 110], fill=GREY)
    for k, side in enumerate([c["left"], c["right"]]):
        cx = w / 4 if k == 0 else 3 * w / 4
        fl = fit_text(d, side[0].upper(), F_BOLD, w / 2 - 120, 46)
        tw = d.textlength(side[0].upper(), font=fl)
        d.text((cx - tw / 2, h / 2 - 150), side[0].upper(), font=fl, fill=WHITE)
        fv = fit_text(d, side[1], F_BLACK, w / 2 - 120, 150)
        tw = d.textlength(side[1], font=fv)
        d.text((cx - tw / 2, h / 2 - 70), side[1], font=fv, fill=RED if k == c.get("hl", 1) else WHITE)
    fv = font(F_BLACK, 60)
    d.ellipse([w / 2 - 55, h / 2 - 25, w / 2 + 55, h / 2 + 85], fill=YELLOW)
    tw = d.textlength("VS", font=fv)
    d.text((w / 2 - tw / 2, h / 2 - 3), "VS", font=fv, fill=BLACK)
    return img


CARDS = dict(big=card_big, bars=card_bars, pct=card_pct, timeline=card_timeline,
             boxes=card_boxes, doc=card_doc, text=card_text, vs=card_vs)


# ---------------------------------------------------------------- images
def prep_photo(src, dst, bw=False):
    im = Image.open(src)
    im = ImageOps.exif_transpose(im).convert("RGB")
    tw, th = int(W * OVER), int(H * OVER)
    ar = im.width / im.height
    if ar >= 1.25:
        im = ImageOps.fit(im, (tw, th), Image.LANCZOS)
    else:  # portrait / square: blurred fill background
        bg = ImageOps.fit(im, (tw, th), Image.LANCZOS).filter(ImageFilter.GaussianBlur(40))
        bg = Image.blend(bg, Image.new("RGB", bg.size, (0, 0, 0)), 0.45)
        fg = ImageOps.contain(im, (tw, th - 40), Image.LANCZOS)
        bg.paste(fg, ((tw - fg.width) // 2, (th - fg.height) // 2))
        im = bg
    if bw:
        im = ImageOps.grayscale(im).convert("RGB")
    im.save(dst, quality=92)


def lower_third(text, path):
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f = font(F_BOLD, 34)
    tw = d.textlength(text, font=f)
    d.rectangle([60, H - 140, 60 + tw + 60, H - 80], fill=(0, 0, 0, 200))
    d.rectangle([60, H - 140, 72, H - 80], fill=RED + (255,))
    d.text((92, H - 132), text, font=f, fill=WHITE + (255,))
    img.save(path)


# ---------------------------------------------------------------- shots
X264 = "-c:v libx264 -preset veryfast -crf 21 -pix_fmt yuv420p -r 30 -an"


def render_still(img_path, frames, out, motion, overlay=None):
    D = frames / FPS
    ow, oh = int(W * OVER), int(H * OVER)
    if motion == "zin":
        vf = (f"scale=w='{W}*(1.0+0.10*t/{D:.3f})':h=-2:eval=frame,"
              f"crop={W}:{H}:(iw-{W})/2:(ih-{H})/2")
        src = f"-loop 1 -framerate {FPS} -i {img_path}"
        vf = f"scale={W}:{H}," + vf
    elif motion == "zout":
        vf = (f"scale=w='{W}*(1.10-0.10*t/{D:.3f})':h=-2:eval=frame,"
              f"crop={W}:{H}:(iw-{W})/2:(ih-{H})/2")
        src = f"-loop 1 -framerate {FPS} -i {img_path}"
        vf = f"scale={W}:{H}," + vf
    else:
        sx = (ow - W)
        sy = (oh - H) / 2
        xe = f"{sx}*t/{D:.3f}" if motion == "pr" else f"{sx}-{sx}*t/{D:.3f}"
        vf = f"crop={W}:{H}:'{xe}':{sy:.0f}"
        src = f"-loop 1 -framerate {FPS} -i {img_path}"
    if overlay:
        cmd = (f"ffmpeg -y -v error {src} -i {overlay} -filter_complex "
               f"\"[0:v]{vf},setsar=1[b];[b][1:v]overlay=0:0,format=yuv420p\" -frames:v {frames} {X264} {out}")
    else:
        cmd = f"ffmpeg -y -v error {src} -vf \"{vf},setsar=1,format=yuv420p\" -frames:v {frames} {X264} {out}"
    sh(cmd)


def render_video(url, frames, out, seed, bw=False, overlay=None):
    D = frames / FPS
    try:
        dur = float(sh(f"ffprobe -v error -show_entries format=duration -of csv=p=0 '{url}'").strip() or 0)
    except Exception:
        dur = 0
    rnd = random.Random(seed)
    start = rnd.uniform(dur * 0.08, max(dur * 0.08, dur * 0.75 - D)) if dur > D + 4 else 0
    vf = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},setsar=1"
    if bw:
        vf += ",hue=s=0"
    if overlay:
        cmd = (f"ffmpeg -y -v error -ss {start:.2f} -i '{url}' -i {overlay} -filter_complex "
               f"\"[0:v]{vf}[b];[b][1:v]overlay=0:0,format=yuv420p\" -frames:v {frames} {X264} {out}")
    else:
        cmd = f"ffmpeg -y -v error -ss {start:.2f} -i '{url}' -vf \"{vf},format=yuv420p\" -frames:v {frames} {X264} {out}"
    sh(cmd)
    n = int(sh(f"ffprobe -v error -count_frames -select_streams v:0 -show_entries stream=nb_read_frames -of csv=p=0 {out}").strip() or 0)
    if n < frames:
        raise RuntimeError(f"short video clip {n}<{frames}")


# ---------------------------------------------------------------- TTS
async def tts_all(segs, outdir, voice, rate):
    import edge_tts
    sem = asyncio.Semaphore(6)

    async def one(i, text):
        p = f"{outdir}/vo_{i:03d}.mp3"
        if os.path.exists(p) and os.path.getsize(p) > 1000:
            return
        async with sem:
            for attempt in range(4):
                try:
                    await edge_tts.Communicate(text, voice, rate=rate).save(p)
                    return
                except Exception as e:
                    print("tts retry", i, e, flush=True)
                    await asyncio.sleep(2 + attempt * 3)
            raise RuntimeError(f"tts failed {i}")

    await asyncio.gather(*[one(i, s["vo"]) for i, s in enumerate(segs)])


def dur_of(p):
    return float(sh(f"ffprobe -v error -show_entries format=duration -of csv=p=0 {p}").strip())


# ---------------------------------------------------------------- main
def main(spec_path, out):
    spec = json.load(open(spec_path))
    os.makedirs(out, exist_ok=True)
    tmp = f"{out}/tmp"
    os.makedirs(tmp, exist_ok=True)
    segs = spec["segments"]
    voice = spec.get("voice", "en-US-ChristopherNeural")
    rate = spec.get("rate", "+6%")
    gap = spec.get("gap", 0.28)
    print(f"[1/4] TTS {len(segs)} segments", flush=True)
    asyncio.run(tts_all(segs, tmp, voice, rate))

    commons = Commons(tmp)
    # pre-warm searches in parallel
    qs = set()
    for s in segs:
        for v in s["v"]:
            if "q" in v:
                qs.add((v["q"], "i"))
            if "vq" in v:
                qs.add((v["vq"], "v"))
    for fb in spec.get("fallback", []):
        qs.add((fb, "i"))
    with cf.ThreadPoolExecutor(6) as ex:
        list(ex.map(lambda a: commons.search(*a), qs))
    print(f"[2/4] searched {len(qs)} queries", flush=True)

    # plan shots
    shots = []  # dict(seg, frames, kind, ...)
    seg_frames = []
    rnd = random.Random(spec.get("seed", 7))
    motions = ["pr", "zin", "pl", "zout"]
    fb_i = 0
    for si, s in enumerate(segs):
        vo = dur_of(f"{tmp}/vo_{si:03d}.mp3")
        total = vo + gap
        items = list(s["v"])
        media_items = [v for v in items if "card" not in v]
        maxshot = s.get("maxshot", spec.get("maxshot", 4.2))
        while total / len(items) > maxshot and media_items:
            items.append(media_items[(len(items) - len(s["v"])) % len(media_items)])
        weights = [1.5 if "card" in v else 1.0 for v in items]
        tf = round(total * FPS)
        fr = [max(int(tf * w / sum(weights)), 12) for w in weights]
        fr[-1] = tf - sum(fr[:-1])
        seg_frames.append(tf)
        for v, f in zip(items, fr):
            shot = dict(seg=si, frames=f, spec=v, idx=len(shots), motion=motions[len(shots) % 4])
            if "card" not in v:
                it = None
                if "vq" in v:
                    it = commons.take(v["vq"], "v")
                if not it and "q" in v:
                    it = commons.take(v["q"], "i")
                for alt in v.get("alt", []):
                    if it:
                        break
                    commons.search(alt, "i")
                    it = commons.take(alt, "i")
                tries = 0
                while not it and spec.get("fallback") and tries < len(spec["fallback"]):
                    it = commons.take(spec["fallback"][fb_i % len(spec["fallback"])], "i")
                    fb_i += 1
                    tries += 1
                shot["media"] = it
            shots.append(shot)
    print(f"[3/4] planned {len(shots)} shots, {sum(seg_frames)/FPS:.1f}s", flush=True)

    def do_shot(sh_):
        i, f, v = sh_["idx"], sh_["frames"], sh_["spec"]
        outp = f"{tmp}/shot_{i:04d}.mp4"
        if os.path.exists(outp):
            return None
        ov = None
        if v.get("lower"):
            ov = f"{tmp}/lt_{i:04d}.png"
            lower_third(v["lower"], ov)
        if "card" in v:
            p = f"{tmp}/card_{i:04d}.png"
            CARDS[v["card"]["t"]](v["card"]).save(p)
            render_still(p, f, outp, "zin", ov)
            return None
        it = sh_.get("media")
        if it and it["kind"] == "v":
            try:
                render_video(it["url"], f, outp, i, v.get("bw", False), ov)
                return it
            except Exception as e:
                print("video fail -> still", it["title"], str(e)[:200], flush=True)
                it = commons.take(v.get("q") or (spec.get("fallback") or ["city"])[0], "i")
        if it:
            raw = f"{tmp}/raw_{i:04d}"
            try:
                http_get(it["url"], raw)
                p = f"{tmp}/img_{i:04d}.jpg"
                prep_photo(raw, p, v.get("bw", False))
                render_still(p, f, outp, sh_["motion"], ov)
                return it
            except Exception as e:
                print("img fail", it["title"], str(e)[:200], flush=True)
        # last resort: text card with the VO keyword
        p = f"{tmp}/card_{i:04d}.png"
        card_text(dict(text=(v.get("q") or "").upper()[:28] or "…")).save(p)
        render_still(p, f, outp, "zin", ov)
        return None

    used = []
    with cf.ThreadPoolExecutor(int(spec.get("workers", 6))) as ex:
        for k, r in enumerate(ex.map(do_shot, shots)):
            if r:
                used.append(r)
            if k % 20 == 0:
                print(f"  shot {k}/{len(shots)}", flush=True)

    # audio: each VO padded to its segment length
    print("[4/4] mux", flush=True)
    with open(f"{tmp}/alist.txt", "w") as fa:
        for si, tf in enumerate(seg_frames):
            wav = f"{tmp}/seg_{si:03d}.wav"
            sh(f"ffmpeg -y -v error -i {tmp}/vo_{si:03d}.mp3 -af apad -t {tf / FPS:.4f} -ar 48000 -ac 2 {wav}")
            fa.write(f"file '{os.path.abspath(wav)}'\n")
    with open(f"{tmp}/vlist.txt", "w") as fv:
        for s_ in shots:
            fv.write(f"file '{os.path.abspath(tmp)}/shot_{s_['idx']:04d}.mp4'\n")
    sh(f"ffmpeg -y -v error -f concat -safe 0 -i {tmp}/vlist.txt -c copy {tmp}/video.mp4")
    sh(f"ffmpeg -y -v error -f concat -safe 0 -i {tmp}/alist.txt -c copy {tmp}/voice.wav")
    music = spec.get("music")
    final = f"{out}/{spec['id']}.mp4"
    if music and os.path.exists(music):
        sh(f"ffmpeg -y -v error -i {tmp}/video.mp4 -i {tmp}/voice.wav -stream_loop -1 -i {music} "
           f"-filter_complex \"[2:a]volume=0.10[m];[1:a][m]amix=inputs=2:duration=first:dropout_transition=0[a]\" "
           f"-map 0:v -map [a] -c:v copy -c:a aac -b:a 160k -movflags +faststart {final}")
    else:
        sh(f"ffmpeg -y -v error -i {tmp}/video.mp4 -i {tmp}/voice.wav -map 0:v -map 1:a -c:v copy "
           f"-c:a aac -b:a 160k -shortest -movflags +faststart {final}")

    seen, lines = set(), []
    for it in used:
        if it["page"] in seen:
            continue
        seen.add(it["page"])
        lines.append(f"- {it['title']} — {it['artist'] or 'unknown'} — {it['license']} — {it['page']}")
    with open(f"{out}/{spec['id']}-credits.txt", "w") as f:
        f.write(f"{spec.get('title', spec['id'])}\nVisuals: Wikimedia Commons (licenses below). "
                f"Voice: Microsoft neural TTS ({voice}).\n\n" + "\n".join(lines) + "\n")
    print(f"DONE {final} {dur_of(final):.1f}s, {len(lines)} credited files", flush=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
