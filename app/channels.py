"""Channel watch: the latest uploads of a few YouTube channels, so a new episode is one click from clipping."""

import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.youtube import _options

DEFAULT_CHANNELS = ["@rajshamani", "@BeerBiceps"]
HANDLE = re.compile(r"^@[A-Za-z0-9._-]{3,60}$")
LATEST = 6
CACHE_S = 30 * 60
MAX_CHANNELS = 12
_cache: dict[str, tuple[float, dict[str, Any]]] = {}  # ponytail: in-memory, refetched after a restart


def clean_channels(lines: list[str], limit: int = MAX_CHANNELS) -> list[str]:
    """Accept '@handle', 'handle' or a youtube.com/@handle link; drop anything else and repeats."""
    out = []
    for line in lines:
        text = line.strip().rstrip("/")
        text = text.split("youtube.com/", 1)[-1].split("/", 1)[0]
        handle = text if text.startswith("@") else f"@{text}"
        if HANDLE.match(handle) and handle.lower() not in {h.lower() for h in out}:
            out.append(handle)
    return out[:limit]


def _fetch(handle: str) -> dict[str, Any]:
    import yt_dlp

    url = f"https://www.youtube.com/{handle}/videos"
    with yt_dlp.YoutubeDL(_options(extract_flat=True, playlist_items=f"1:{LATEST}", skip_download=True)) as ydl:
        info = ydl.extract_info(url, download=False)
    videos = [{"id": e["id"], "title": e.get("title") or "", "duration": e.get("duration") or 0,
               "views": e.get("view_count"), "url": f"https://www.youtube.com/watch?v={e['id']}"}
              for e in info.get("entries") or [] if e.get("id") and e.get("live_status") not in ("is_upcoming", "is_live")]
    return {"handle": handle, "channel": info.get("channel") or handle, "videos": videos, "error": None}


def latest(handle: str) -> dict[str, Any]:
    hit = _cache.get(handle.lower())
    if hit and time.time() - hit[0] < CACHE_S:
        return hit[1]
    try:
        result = _fetch(handle)
    except Exception as error:  # one broken channel must not hide the others
        return {"handle": handle, "channel": handle, "videos": [], "error": str(error)[:200]}
    _cache[handle.lower()] = (time.time(), result)
    return result


def latest_for(handles: list[str]) -> list[dict[str, Any]]:
    with ThreadPoolExecutor(max_workers=4) as pool:
        return list(pool.map(latest, handles))
