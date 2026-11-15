"""Thumbnail: generated scene + big outlined text from the title/thumbnail pair."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

FONT_CANDIDATES = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "DejaVuSans-Bold.ttf",
                   "/Library/Fonts/Arial Bold.ttf", "C:/Windows/Fonts/arialbd.ttf"]


def load_font(size: int, path: str | None = None):
    for p in ([path] if path else []) + FONT_CANDIDATES:
        try:
            return ImageFont.truetype(p, size)
        except (OSError, TypeError):
            continue
    return ImageFont.load_default()


def make_thumbnail(scene: Path, text: str, dest: Path, font_path: str | None = None) -> Path:
    W, H = 1280, 720
    img = ImageOps.fit(Image.open(scene).convert("RGB"), (W, H))
    d = ImageDraw.Draw(img)
    text = text.upper()
    size = 180
    while size > 40:
        font = load_font(size, font_path)
        box = d.textbbox((0, 0), text, font=font, stroke_width=size // 12)
        if box[2] - box[0] <= W * 0.9:
            break
        size -= 8
    tw = box[2] - box[0]
    x, y = (W - tw) // 2, int(H * 0.06)
    d.text((x + 6, y + 8), text, font=font, fill=(0, 0, 0), stroke_width=size // 12, stroke_fill=(0, 0, 0))
    d.text((x, y), text, font=font, fill=(255, 221, 0), stroke_width=size // 12, stroke_fill=(30, 20, 90))
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, "JPEG", quality=90)
    return dest
