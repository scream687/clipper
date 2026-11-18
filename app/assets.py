"""Brand assets shared by every clip: a music library (data/music/) and one logo (data/logo.png)."""

import io
import json
import re
import subprocess
from pathlib import Path
from typing import BinaryIO

from app import DATA_DIR, FFPROBE

MUSIC_DIR = DATA_DIR / "music"
LOGO_FILE = DATA_DIR / "logo.png"
MUSIC_TYPES = {".mp3", ".m4a", ".aac", ".wav"}
MUSIC_NAME = re.compile(r"[A-Za-z0-9_-]{1,80}\.(mp3|m4a|aac|wav)")
MAX_MUSIC_BYTES = 60 * 1024 * 1024
MAX_LOGO_BYTES = 8 * 1024 * 1024
LOGO_PX = 512


def _duration(path: Path) -> float | None:
    out = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_type:format=duration",
                          "-of", "json", str(path)], capture_output=True, text=True)
    try:
        info = json.loads(out.stdout or "{}")
        return float(info["format"]["duration"]) if info.get("streams") else None
    except (KeyError, ValueError):
        return None


def save_music(filename: str, data: BinaryIO) -> str:
    """Store an uploaded track under a safe name; refuse anything ffprobe cannot read as audio."""
    path = Path(filename)
    if path.suffix.lower() not in MUSIC_TYPES:
        raise ValueError("Upload an MP3, M4A, AAC or WAV file.")
    stem = re.sub(r"-+", "-", re.sub(r"[^A-Za-z0-9_-]", "-", path.stem)).strip("-")[:70] or "track"
    MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    target = MUSIC_DIR / f"{stem}{path.suffix.lower()}"
    partial = target.with_name(target.name + ".partial")
    written = 0
    with open(partial, "wb") as out:
        while chunk := data.read(1024 * 1024):
            written += len(chunk)
            if written > MAX_MUSIC_BYTES:
                out.close(); partial.unlink(missing_ok=True)
                raise ValueError("That track is over 60 MB.")
            out.write(chunk)
    probe = partial.with_suffix(partial.suffix + path.suffix.lower())  # ffprobe picks the demuxer by extension
    partial.replace(probe)
    if not _duration(probe):
        probe.unlink(missing_ok=True)
        raise ValueError("That file is not audio that can be played.")
    probe.replace(target)
    return target.name


def list_music() -> list[dict]:
    if not MUSIC_DIR.is_dir():
        return []
    return [{"name": p.name, "duration": _duration(p) or 0.0} for p in sorted(MUSIC_DIR.iterdir()) if MUSIC_NAME.fullmatch(p.name)]


def music_path(name: str) -> Path:
    """The name format is the guard against path traversal."""
    path = MUSIC_DIR / name
    if not MUSIC_NAME.fullmatch(name) or not path.is_file():
        raise KeyError(name)
    return path


def delete_music(name: str) -> None:
    music_path(name).unlink()


def save_logo(data: BinaryIO) -> None:
    """Re-encode any raster image as a PNG at most 512 px wide: nothing the browser or ffmpeg gets is user bytes."""
    from PIL import Image, UnidentifiedImageError
    raw = data.read(MAX_LOGO_BYTES + 1)
    if len(raw) > MAX_LOGO_BYTES:
        raise ValueError("That image is over 8 MB.")
    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except (UnidentifiedImageError, OSError) as error:
        raise ValueError("Upload a PNG, JPG or WebP image.") from error
    image = image.convert("RGBA")
    image.thumbnail((LOGO_PX, LOGO_PX))
    LOGO_FILE.parent.mkdir(parents=True, exist_ok=True)
    partial = LOGO_FILE.with_suffix(".partial.png")
    image.save(partial, "PNG")
    partial.replace(LOGO_FILE)


def logo_size() -> tuple[int, int] | None:
    if not LOGO_FILE.is_file():
        return None
    from PIL import Image
    with Image.open(LOGO_FILE) as image:
        return image.size


def delete_logo() -> None:
    LOGO_FILE.unlink(missing_ok=True)
