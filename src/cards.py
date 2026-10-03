"""Data card generation — Pillow-based visual cards.

Extracted from the original render.py. All 8 card types preserved:
big, bars, pct, timeline, boxes, doc, text, vs.
"""

from __future__ import annotations

import math
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

W, H, FPS = 1280, 720, 30
OVER = 1.14

RED = (226, 35, 26)
WHITE = (245, 245, 245)
BLACK = (8, 8, 10)
YELLOW = (245, 197, 24)
GREY = (120, 120, 128)

FONT_DIRS = [
    "/usr/share/fonts",
    "/usr/local/share/fonts",
    os.path.expanduser("~/.fonts"),
    os.path.expanduser("~/.local/share/fonts"),
]


def _find_font(names: list[str]) -> str | None:
    for d in FONT_DIRS:
        for root, _, files in os.walk(d):
            for f in files:
                for n in names:
                    if f.lower() == n.lower():
                        return os.path.join(root, f)
    return None


F_BLACK = _find_font([
    "Montserrat-Black.ttf", "Montserrat-ExtraBold.ttf",
    "Montserrat-Bold.ttf", "Metropolis-Black.otf",
    "Metropolis-Bold.otf", "DejaVuSans-Bold.ttf",
])
F_BOLD = _find_font([
    "Montserrat-Bold.ttf", "Metropolis-Bold.otf", "DejaVuSans-Bold.ttf",
]) or F_BLACK
F_REG = _find_font([
    "Montserrat-Medium.ttf", "Montserrat-Regular.ttf",
    "Metropolis-Medium.otf", "DejaVuSans.ttf",
]) or F_BOLD
F_SERIF = _find_font([
    "DejaVuSerif-Bold.ttf", "LiberationSerif-Bold.ttf",
]) or F_BOLD


def _font(path: str | None, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def _grid_bg(dark: bool = True) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    w, h = int(W * OVER), int(H * OVER)
    img = Image.new("RGB", (w, h), BLACK if dark else (238, 238, 236))
    d = ImageDraw.Draw(img)
    c = (34, 34, 40) if dark else (210, 210, 214)
    for x in range(0, w, 48):
        d.line([(x, 0), (x, h)], fill=c, width=1)
    for y in range(0, h, 48):
        d.line([(0, y), (w, y)], fill=c, width=1)
    return img, d


def _fit_text(d: ImageDraw.ImageDraw, text: str, path: str | None,
              max_w: int, start: int) -> ImageFont.FreeTypeFont:
    s = start
    while s > 14:
        f = _font(path, s)
        if d.textlength(text, font=f) <= max_w:
            return f
        s -= 4
    return _font(path, 14)


def _center_text(d: ImageDraw.ImageDraw, y: float, text: str,
                 f: ImageFont.FreeTypeFont, fill: tuple, w: int):
    tw = d.textlength(text, font=f)
    d.text(((w - tw) / 2, y), text, font=f, fill=fill)


def card_big(c: dict) -> Image.Image:
    img, d = _grid_bg(True)
    w, h = img.size
    f = _fit_text(d, c["text"], F_BLACK, w - 220, 230)
    bb = d.textbbox((0, 0), c["text"], font=f)
    th = bb[3] - bb[1]
    y = h / 2 - th / 2 - 50 - bb[1]
    _center_text(d, y, c["text"], f,
                 tuple(c["color"]) if c.get("color") else WHITE, w)
    d.rectangle([w / 2 - 170, y + bb[3] + 30, w / 2 + 170, y + bb[3] + 44],
                fill=RED)
    if c.get("sub"):
        fs = _fit_text(d, c["sub"].upper(), F_BOLD, w - 260, 52)
        _center_text(d, y + bb[3] + 70, c["sub"].upper(), fs, WHITE, w)
    return img


def card_bars(c: dict) -> Image.Image:
    img, d = _grid_bg(True)
    w, h = img.size
    items = c["items"]
    vmax = max(i[1] for i in items) or 1
    ft = _fit_text(d, c.get("title", "").upper(), F_BOLD, w - 240, 48)
    d.text((140, 120), c.get("title", "").upper(), font=ft, fill=WHITE)
    n = len(items)
    top, bot = 230, h - 130
    bh = min(110, (bot - top) / n * 0.62)
    gap = (bot - top - bh * n) / max(n, 1)
    fl = _font(F_BOLD, 34)
    fv = _font(F_BLACK, int(bh * 0.62))
    lab_w = max(d.textlength(i[0].upper(), font=fl) for i in items) + 40
    x0 = 140 + lab_w
    maxlen = w - x0 - 260
    for k, it in enumerate(items):
        y = top + k * (bh + gap) + gap / 2
        d.text((140, y + bh / 2 - 20), it[0].upper(), font=fl, fill=WHITE)
        ln = max(8, maxlen * it[1] / vmax)
        col = RED if k == c.get("hl", 0) else GREY
        d.rectangle([x0, y, x0 + ln, y + bh], fill=col)
        d.text((x0 + ln + 22, y + bh / 2 - fv.size / 1.6),
               it[2], font=fv, fill=WHITE)
    return img


def card_pct(c: dict) -> Image.Image:
    img, d = _grid_bg(True)
    w, h = img.size
    ft = _fit_text(d, c["title"].upper(), F_BOLD, w - 280, 54)
    _center_text(d, 210, c["title"].upper(), ft, WHITE, w)
    x0, x1, y0, y1 = 170, w - 170, 330, 450
    d.rectangle([x0, y0, x1, y1], outline=WHITE, width=4)
    d.rectangle([x0 + 8, y0 + 8,
                 x0 + 8 + (x1 - x0 - 16) * c["pct"] / 100, y1 - 8],
                fill=RED)
    _center_text(d, 490, c.get("label", f"{c['pct']}%"),
                 _font(F_BLACK, 110), WHITE, w)
    return img


def card_timeline(c: dict) -> Image.Image:
    img, d = _grid_bg(False)
    w, h = img.size
    ft = _fit_text(d, c.get("title", "").upper(), F_BOLD, w - 240, 50)
    _center_text(d, 130, c.get("title", "").upper(), ft, (20, 20, 24), w)
    pts = c["points"]
    y = h / 2 + 20
    x0, x1 = 170, w - 170
    d.line([(x0, y), (x1, y)], fill=(30, 30, 34), width=6)
    n = len(pts)
    fy = _font(F_BLACK, 46)
    fl = _font(F_BOLD, 26)
    for k, (yr, lab) in enumerate(pts):
        x = x0 + (x1 - x0) * (k / (n - 1) if n > 1 else 0.5)
        hl = k == c.get("hl", n - 1)
        r = 26 if hl else 16
        d.polygon([(x, y - r), (x + r, y), (x, y + r), (x - r, y)],
                  fill=RED if hl else (30, 30, 34))
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
            d.text((x - lw / 2, y + 50 + j * 34), ln, font=fl,
                   fill=RED if hl else (40, 40, 46))
    return img


def card_boxes(c: dict) -> Image.Image:
    img, d = _grid_bg(True)
    w, h = img.size
    if c.get("parent"):
        fp = _font(F_BLACK, 70)
        _center_text(d, 120, c["parent"].upper(), fp, WHITE, w)
        d.rectangle([w / 2 - 120, 215, w / 2 + 120, 225], fill=RED)
    items = c["items"]
    cols = min(4, len(items)) if len(items) != 5 else 5
    rows = math.ceil(len(items) / cols)
    bw, bh = (w - 260) / cols - 24, 110
    top = 290 if c.get("parent") else (h - rows * (bh + 30)) / 2
    for k, it in enumerate(items):
        r_, c_ = divmod(k, cols)
        x = 130 + c_ * (bw + 24)
        y = top + r_ * (bh + 30)
        hl = k in c.get("hl", [])
        d.rectangle([x, y, x + bw, y + bh],
                    outline=RED if hl else WHITE, width=4,
                    fill=(60, 12, 10) if hl else None)
        f2 = _fit_text(d, it.upper(), F_BOLD, bw - 24, 34)
        tw = d.textlength(it.upper(), font=f2)
        d.text((x + (bw - tw) / 2, y + bh / 2 - f2.size / 1.6),
               it.upper(), font=f2, fill=WHITE)
    if c.get("caption"):
        fc = _fit_text(d, c["caption"].upper(), F_BOLD, w - 260, 40)
        _center_text(d, h - 150, c["caption"].upper(), fc, RED, w)
    return img


def card_doc(c: dict) -> Image.Image:
    w, h = int(W * OVER), int(H * OVER)
    img = Image.new("RGB", (w, h), (28, 28, 30))
    d = ImageDraw.Draw(img)
    px0, px1 = w * 0.2, w * 0.8
    d.rectangle([px0 + 14, 70 + 14, px1 + 14, h + 40], fill=(10, 10, 12))
    d.rectangle([px0, 70, px1, h + 40], fill=(248, 246, 240))
    fh = _fit_text(d, c["head"].upper(), F_SERIF, px1 - px0 - 80, 34)
    tw = d.textlength(c["head"].upper(), font=fh)
    d.text(((w - tw) / 2, 130), c["head"].upper(), font=fh, fill=(15, 15, 15))
    d.line([(px0 + 60, 190), (px1 - 60, 190)], fill=(15, 15, 15), width=2)
    fb = _font(F_SERIF, 30)
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
        d.line([(px0 + 70, yy), (px1 - 70 - (k % 3) * 60, yy)],
               fill=(190, 190, 186), width=8)
    if c.get("stamp"):
        fs = _font(F_BLACK, 64)
        st = Image.new("RGBA",
                        (int(d.textlength(c["stamp"], font=fs)) + 60, 110),
                        (0, 0, 0, 0))
        sd = ImageDraw.Draw(st)
        sd.rectangle([4, 4, st.width - 4, 106],
                     outline=RED + (255,), width=7)
        sd.text((30, 14), c["stamp"], font=fs, fill=RED + (255,))
        st = st.rotate(12, expand=True)
        img.paste(st, (int(px1 - st.width - 20), int(h * 0.55)), st)
    return img


def card_text(c: dict) -> Image.Image:
    img, d = _grid_bg(True)
    w, h = img.size
    lines = c["text"].split("\n")
    fs = [_fit_text(d, ln, F_BLACK, w - 260,
                    120 if len(lines) == 1 else 92) for ln in lines]
    total = sum(f.size * 1.2 for f in fs)
    y = (h - total) / 2 - 20
    for k, (ln, f) in enumerate(zip(lines, fs)):
        col = RED if k in c.get("red", []) else WHITE
        _center_text(d, y, ln, f, col, w)
        y += f.size * 1.2
    if c.get("sub"):
        fsb = _fit_text(d, c["sub"].upper(), F_BOLD, w - 300, 40)
        _center_text(d, y + 30, c["sub"].upper(), fsb, GREY, w)
    return img


def card_vs(c: dict) -> Image.Image:
    img, d = _grid_bg(True)
    w, h = img.size
    d.rectangle([w / 2 - 3, 110, w / 2 + 3, h - 110], fill=GREY)
    for k, side in enumerate([c["left"], c["right"]]):
        cx = w / 4 if k == 0 else 3 * w / 4
        fl = _fit_text(d, side[0].upper(), F_BOLD, w / 2 - 120, 46)
        tw = d.textlength(side[0].upper(), font=fl)
        d.text((cx - tw / 2, h / 2 - 150), side[0].upper(), font=fl, fill=WHITE)
        fv = _fit_text(d, side[1], F_BLACK, w / 2 - 120, 150)
        tw = d.textlength(side[1], font=fv)
        d.text((cx - tw / 2, h / 2 - 70), side[1], font=fv,
               fill=RED if k == c.get("hl", 1) else WHITE)
    fv = _font(F_BLACK, 60)
    d.ellipse([w / 2 - 55, h / 2 - 25, w / 2 + 55, h / 2 + 85], fill=YELLOW)
    tw = d.textlength("VS", font=fv)
    d.text((w / 2 - tw / 2, h / 2 - 3), "VS", font=fv, fill=BLACK)
    return img


CARDS = dict(
    big=card_big, bars=card_bars, pct=card_pct, timeline=card_timeline,
    boxes=card_boxes, doc=card_doc, text=card_text, vs=card_vs,
)


def render_card(spec: dict) -> Image.Image:
    return CARDS[spec["t"]](spec)


def lower_third(text: str, path: str):
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f = _font(F_BOLD, 34)
    tw = d.textlength(text, font=f)
    d.rectangle([60, H - 140, 60 + tw + 60, H - 80], fill=(0, 0, 0, 200))
    d.rectangle([60, H - 140, 72, H - 80], fill=RED + (255,))
    d.text((92, H - 132), text, font=f, fill=WHITE + (255,))
    img.save(path)


def prepare_photo(src: str, dst: str, bw: bool = False):
    im = Image.open(src)
    im = ImageOps.exif_transpose(im).convert("RGB")
    tw, th = int(W * OVER), int(H * OVER)
    ar = im.width / im.height
    if ar >= 1.25:
        im = ImageOps.fit(im, (tw, th), Image.LANCZOS)
    else:
        bg = ImageOps.fit(im, (tw, th), Image.LANCZOS).filter(
            ImageFilter.GaussianBlur(40))
        bg = Image.blend(bg, Image.new("RGB", bg.size, (0, 0, 0)), 0.45)
        fg = ImageOps.contain(im, (tw, th - 40), Image.LANCZOS)
        bg.paste(fg, ((tw - fg.width) // 2, (th - fg.height) // 2))
        im = bg
    if bw:
        im = ImageOps.grayscale(im).convert("RGB")
    im.save(dst, quality=92)
