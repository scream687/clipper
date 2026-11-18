"""Stock B-roll from Pexels (free API key in Settings): a portrait clip for a query, else a portrait photo.
Results are cached in data/broll/ by query, so the same B-roll is never downloaded twice."""

import hashlib
import json
import urllib.parse
import urllib.request
from pathlib import Path

from app import DATA_DIR

CACHE = DATA_DIR / "broll"
MAX_BYTES = 80 * 1024 * 1024
TIMEOUT_S = 30


def _get(url: str, key: str) -> dict:
    request = urllib.request.Request(url, headers={"Authorization": key, "User-Agent": "clipper"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:  # noqa: S310 (fixed https host)
        return json.loads(response.read(2 * 1024 * 1024))


def best_video_file(video: dict, height: int = 1920) -> str | None:
    """The mp4 link of a portrait rendition closest to `height`, at least 720 px tall. Pure, unit-tested."""
    files = [f for f in video.get("video_files", []) if f.get("file_type") == "video/mp4" and f.get("link")
             and (f.get("height") or 0) >= 720 and (f.get("height") or 0) >= (f.get("width") or 0)]
    if not files:
        return None
    return min(files, key=lambda f: abs(f["height"] - height))["link"]


def _download(url: str, target: Path) -> Path:
    if not url.startswith("https://"):
        raise ValueError("Pexels gave a link that is not https")
    partial = target.with_suffix(target.suffix + ".partial")
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "clipper"}), timeout=TIMEOUT_S) as r:  # noqa: S310
        data = r.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("That B-roll file is too large")
    partial.write_bytes(data)
    partial.replace(target)
    return target


def fetch(query: str, key: str) -> tuple[Path, str] | None:
    """(file, 'video' | 'image') for a query, or None when Pexels has nothing portrait for it."""
    if not key or not query.strip():
        return None
    CACHE.mkdir(parents=True, exist_ok=True)
    stem = hashlib.sha1(query.strip().lower().encode()).hexdigest()[:16]
    for suffix, kind in ((".mp4", "video"), (".jpg", "image")):
        if (CACHE / f"{stem}{suffix}").is_file():
            return CACHE / f"{stem}{suffix}", kind
    q = urllib.parse.quote(query.strip())
    videos = _get(f"https://api.pexels.com/videos/search?query={q}&orientation=portrait&per_page=6", key).get("videos", [])
    for video in videos:
        link = best_video_file(video)
        if link:
            return _download(link, CACHE / f"{stem}.mp4"), "video"
    photos = _get(f"https://api.pexels.com/v1/search?query={q}&orientation=portrait&per_page=3", key).get("photos", [])
    if photos and photos[0].get("src", {}).get("portrait"):
        return _download(photos[0]["src"]["portrait"], CACHE / f"{stem}.jpg"), "image"
    return None
