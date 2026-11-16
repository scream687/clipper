"""Clipper web app.

Importing this package pins the vendored core to the caption-capable ffmpeg
through the CLIPPER_FFMPEG environment variable.
"""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

# Homebrew's default ffmpeg is built without libass/freetype, so it cannot burn
# captions or credit text. The keg-only ffmpeg-full can.
FFMPEG_BIN = Path(os.environ.get("CLIPPER_FFMPEG_BIN", "/opt/homebrew/opt/ffmpeg-full/bin"))
FFMPEG = str(FFMPEG_BIN / "ffmpeg")
FFPROBE = str(FFMPEG_BIN / "ffprobe")
if not Path(FFMPEG).exists():
    raise RuntimeError(f"ffmpeg-full not found at {FFMPEG}. Install it with: brew install ffmpeg-full")
os.environ.setdefault("CLIPPER_FFMPEG", FFMPEG)


def cookies_path() -> str | None:
    """Optional drop-in: a cookies.txt at the project root, only if YouTube demands sign-in."""
    path = ROOT / "cookies.txt"
    return str(path) if path.exists() else None
