"""Score Clipper's picks against the Shorts the creator cut from the same episode themselves.

A creator's own Shorts are human-picked best moments. Each one is located in the episode transcript by its
captions; the score is the share of them that a Clipper clip covers, out of 10.
"""

import json
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from app import DATA_DIR
from app.model import Clip, Video, Word
from app.quotes import CLUSTER_S, cluster, index_words
from app.youtube import _options, download_caption_words

BENCH_DIR = DATA_DIR / "bench"
MAX_SHORTS = 150         # the channel's latest Shorts; episodes keep getting clipped for weeks
MIN_HITS = 12            # real Shorts match 60+ lines; a sponsor read repeated in a Short matches ~4
REACH_S = CLUSTER_S * 3  # creators stitch Shorts from lines a few minutes apart
COVER = 0.5              # a clip "found" a Short when half of the Short's matched lines are inside it
WORKERS = 6
LogFn = Callable[[str], None]


def _shorts_list(channel_id: str) -> list[dict[str, Any]]:
    import yt_dlp

    url = f"https://www.youtube.com/channel/{channel_id}/shorts"
    with yt_dlp.YoutubeDL(_options(extract_flat=True, playlist_items=f"1:{MAX_SHORTS}", skip_download=True)) as ydl:
        info = ydl.extract_info(url, download=False)
    return [{"id": e["id"], "title": e.get("title") or ""} for e in info.get("entries") or [] if e.get("id")]


def _captions(short_id: str) -> str:
    work = Path(tempfile.mkdtemp(prefix="bench-"))
    try:
        words = download_caption_words(f"https://www.youtube.com/shorts/{short_id}", None, work)
        return " ".join(w["word"] for w in words)
    except Exception:  # no captions (music-only Short) or blocked: it cannot be placed
        return ""
    finally:
        shutil.rmtree(work, ignore_errors=True)


def channel_shorts(channel_id: str, log: LogFn = print, refresh: bool = False) -> list[dict[str, Any]]:
    """The channel's latest Shorts with their caption text. Cached per channel, shared by all its episodes."""
    cache = BENCH_DIR / f"shorts-{channel_id}.json"
    if cache.exists() and not refresh:
        return json.loads(cache.read_text(encoding="utf-8"))
    shorts = _shorts_list(channel_id)
    log(f"Reading captions of the channel's latest {len(shorts)} Shorts")
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        texts = list(pool.map(_captions, [s["id"] for s in shorts]))
    result = [{**s, "text": t} for s, t in zip(shorts, texts) if t]
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result


def place_shorts(shorts: list[dict[str, Any]], words: list[Word]) -> list[dict[str, Any]]:
    """The Shorts taken from this transcript, with the times of their matched lines."""
    index = index_words(words)
    placed = []
    for short in shorts:
        near = cluster(short["text"], index, MIN_HITS, REACH_S)
        if near:
            placed.append({"id": short["id"], "title": short["title"], "start": near[0], "end": near[-1], "hits": near})
    return placed


def covered(short: dict[str, Any], spans: list[tuple[float, float]]) -> bool:
    """Half of the Short's matched lines fall inside one clip."""
    return any(sum(a <= t <= b for t in short["hits"]) >= COVER * len(short["hits"]) for a, b in spans)


def score(video: Video, clips: list[Clip], shorts: list[dict[str, Any]], top: int | None = None) -> dict[str, Any]:
    """Share of the creator's Shorts covered by the clips (the `top` best by AI score, when given)."""
    kept = sorted((c for c in clips if c.status != "discarded"), key=lambda c: -c.score)[:top]
    spans = [(video.words[c.first_word].start, video.words[c.last_word].end) for c in kept]
    hits = [s for s in shorts if covered(s, spans)]
    rated = [c.rating for c in clips if c.rating]
    return {"creator_shorts": len(shorts), "found": len(hits), "clips": len(kept),
            "score": round(10 * len(hits) / len(shorts), 1) if shorts else None,
            "missed": [s for s in shorts if s not in hits],
            "your_rating": round(sum(rated) / len(rated), 1) if rated else None, "rated": len(rated)}
