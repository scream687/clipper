"""The X/Twitter post template: a post header (avatar, name, verified tick, @handle, the hook as the post text)
above the video. The header is one PNG drawn here and used by both the export and the preview, so they match."""

from PIL import Image, ImageDraw

from app import assets
from app.plan import FONTS, Plan

THEMES = {"light": {"bg": "#FFFFFF", "text": "#0F1419", "muted": "#536471"},
          "dark": {"bg": "#000000", "text": "#E7E9EA", "muted": "#71767B"}}
VERIFIED = "#1D9BF0"


def _font(name: str, size: int):
    from app.plan import _font as cached
    return cached(name, size)


def _tick(draw: ImageDraw.ImageDraw, x: int, y: int, size: int) -> None:
    draw.ellipse((x, y, x + size, y + size), fill=VERIFIED)
    s = size / 24
    draw.line([(x + 6.5 * s, y + 12.5 * s), (x + 10.5 * s, y + 16 * s), (x + 17.5 * s, y + 8.5 * s)], fill="white",
              width=max(2, round(2.6 * s)), joint="curve")


def _avatar(size: int, name: str, theme: dict) -> Image.Image:
    """The logo in a circle, or the name's first letter when there is no logo."""
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    if assets.LOGO_FILE.is_file():
        with Image.open(assets.LOGO_FILE) as logo:
            logo = logo.convert("RGBA")
            side = min(logo.size)
            logo = logo.crop(((logo.width - side) // 2, (logo.height - side) // 2, (logo.width + side) // 2,
                              (logo.height + side) // 2)).resize((size, size), Image.LANCZOS)
        face = Image.new("RGBA", (size, size), theme["muted"])
        face.alpha_composite(logo)
    else:
        face = Image.new("RGBA", (size, size), VERIFIED)
        letter = (name[:1] or "?").upper()
        font = _font("Arial Black", round(size * 0.48))
        ImageDraw.Draw(face).text((size / 2, size / 2), letter, font=font, fill="white", anchor="mm")
    face.putalpha(mask)
    return face


def tweet_header(plan: Plan) -> Image.Image:
    t = plan.tweet
    theme = THEMES[t["theme"]]
    image = Image.new("RGBA", (plan.width, t["height"]), theme["bg"])
    draw = ImageDraw.Draw(image)
    image.alpha_composite(_avatar(t["avatar"], t["name"], theme), (t["pad"], t["top"]))
    text_x = t["pad"] + t["avatar"] + round(t["avatar"] * 0.22)
    name_font, handle_font = _font("Arial Bold", t["name_size"]), _font("Arial", t["handle_size"])
    name_y = t["top"] + round(t["avatar"] * 0.08)
    draw.text((text_x, name_y), t["name"], font=name_font, fill=theme["text"])
    tick = round(t["name_size"] * 0.95)
    _tick(draw, text_x + round(name_font.getlength(t["name"])) + round(tick * 0.3), name_y + round(t["name_size"] * 0.1), tick)
    draw.text((text_x, name_y + round(t["name_size"] * 1.3)), t["handle"], font=handle_font, fill=theme["muted"])
    body_font = _font("Arial", t["text_size"])
    y = t["text_y"]
    for line in t["lines"]:
        draw.text((t["pad"], y), line, font=body_font, fill=theme["text"])
        y += t["line_h"]
    return image
