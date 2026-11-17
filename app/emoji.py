"""iOS emoji as pictures: libass cannot draw colour emoji, so each one is drawn from the Mac's
Apple Color Emoji font into a PNG and overlaid on the video. The browser preview uses the same font."""

import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT = Path("/System/Library/Fonts/Apple Color Emoji.ttc")
FONT_PX = 160  # Apple Color Emoji only has bitmaps at fixed sizes; 160 is the largest
EMOJI_RE = re.compile(
    r"^(?:[\U0001F000-\U0001FAFF☀-➿⬀-⯿←-⇿⌀-⏿]"
    r"[️\U0001F3FB-\U0001F3FF]*(?:‍[\U0001F000-\U0001FAFF☀-➿♀♂⚕][️]?)*)$")


def is_emoji(text: str) -> bool:
    return bool(text) and bool(EMOJI_RE.fullmatch(text))


def emoji_png(emoji: str, px: int, folder: Path) -> Path:
    """The emoji as an RGBA PNG of px x px, cached by codepoints."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / ("-".join(f"{ord(ch):x}" for ch in emoji) + f"_{px}.png")
    if path.exists():
        return path
    font = ImageFont.truetype(str(FONT), FONT_PX)
    canvas = Image.new("RGBA", (FONT_PX + 40, FONT_PX + 40), (0, 0, 0, 0))
    ImageDraw.Draw(canvas).text((20, 20), emoji, font=font, embedded_color=True)
    box = canvas.getbbox()
    if box is None:
        raise RuntimeError(f"The emoji {emoji!r} could not be drawn.")
    glyph = canvas.crop(box)
    glyph.thumbnail((px, px), Image.LANCZOS)
    out = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    out.paste(glyph, ((px - glyph.width) // 2, (px - glyph.height) // 2), glyph)
    out.save(path)
    return path
