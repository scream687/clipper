"""YouTube comments as a signal: timestamps viewers point at, and the most-liked reactions."""

import re
from typing import Any

MAX_COMMENTS = 1000
KEEP_TOP = 40                # most-liked comments shown to the AI
BIN_S = 30                   # timestamp mentions are grouped into 30 s bins
MAX_BINS = 15
TIME_RE = re.compile(r"(?<![\d:])(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?![\d:])")


def parse_times(text: str, duration: float) -> list[int]:
    times = []
    for h, m, s in TIME_RE.findall(text):
        minutes, seconds = int(m), int(s)
        if seconds >= 60 or (h and minutes >= 60):
            continue
        t = int(h or 0) * 3600 + minutes * 60 + seconds
        if 0 < t < duration:
            times.append(t)
    return times


def fetch_comments(url: str, duration: float) -> list[dict[str, Any]]:
    """Top comments (YouTube's 'top' order), trimmed to what the clipper needs."""
    import yt_dlp

    from app.youtube import _options

    opts = _options(skip_download=True, getcomments=True,
                    extractor_args={"youtube": {"max_comments": [str(MAX_COMMENTS), "all", "50"], "comment_sort": ["top"]}})
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    kept = []
    for c in info.get("comments") or []:
        text = (c.get("text") or "").strip()
        if text:
            kept.append({"text": text[:300], "likes": int(c.get("like_count") or 0), "times": parse_times(text, duration)})
    return kept


def _clock(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def comment_note(comments: list[dict[str, Any]]) -> str:
    """Text for the AI: the moments comments point at most, then the most-liked comments."""
    if not comments:
        return ""
    bins: dict[int, list[dict]] = {}
    for c in comments:
        for t in c.get("times", []):
            bins.setdefault(t // BIN_S, []).append(c)
    ranked = sorted(bins.items(), key=lambda kv: (-len(kv[1]), -sum(c["likes"] for c in kv[1])))[:MAX_BINS]
    parts = []
    if ranked:
        parts.append("Moments viewers point at in the comments (most mentioned first):")
        for b, group in ranked:
            best = max(group, key=lambda c: c["likes"])
            parts.append(f"- {_clock(b * BIN_S)}: {len(group)} comment(s), e.g. \"{best['text'][:140]}\"")
    top = sorted(comments, key=lambda c: -c["likes"])[:KEEP_TOP]
    parts.append("Most-liked comments (what resonated; quotes and topics people react to):")
    parts += [f"- ({c['likes']} likes) {c['text'][:200]}" for c in top]
    return "\n".join(parts)


def mentions_in(comments: list[dict[str, Any]], start: float, end: float) -> int:
    return sum(1 for c in comments if any(start <= t <= end for t in c.get("times", [])))
