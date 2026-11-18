"""YouTube comments as a signal: timestamps viewers point at, and the most-liked reactions."""

import re
from typing import Any

from app.model import Word
from app.quotes import index_words, locate
from app.youtube import signed_in

MAX_PARENTS = 5000           # YouTube's top-sorted comments; likes concentrate at the top
MAX_REPLIES_PER_THREAD = 3
MAX_COMMENTS = 8000          # parents + replies
KEEP_TOP = 80                # most-liked comments that point nowhere, shown to the AI for topics
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


@signed_in
def fetch_comments(url: str, duration: float) -> list[dict[str, Any]]:
    """Top comments (YouTube's 'top' order), trimmed to what the clipper needs."""
    import yt_dlp

    from app.youtube import _options

    opts = _options(skip_download=True, getcomments=True,
                    extractor_args={"youtube": {"max_comments": [str(MAX_COMMENTS), str(MAX_PARENTS), "all", str(MAX_REPLIES_PER_THREAD)], "comment_sort": ["top"]}})
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    kept = []
    for c in info.get("comments") or []:
        text = (c.get("text") or "").strip()
        if text:
            kept.append({"text": text[:300], "likes": int(c.get("like_count") or 0), "times": parse_times(text, duration),
                         "reply": c.get("parent") not in (None, "root")})
    return kept


def match_quotes(comments: list[dict[str, Any]], words: list[Word]) -> list[dict[str, Any]]:
    """Every comment, timestamped or not: one that quotes a line gets `quoted` (seconds of that line)."""
    index = index_words(words)
    out = []
    for c in comments:
        spot = locate(c["text"], index)
        out.append({**c, "quoted": [int(spot[0])]} if spot else c)
    return out


def keep_for_ai(comments: list[dict[str, Any]], top: int = KEEP_TOP) -> list[dict[str, Any]]:
    """Comments that point at a moment (timestamp or quote), plus the most-liked of the rest."""
    pointed = [c for c in comments if c.get("times") or c.get("quoted")]
    rest = sorted((c for c in comments if not (c.get("times") or c.get("quoted"))), key=lambda c: -c["likes"])
    return pointed + rest[:top]


def _points(c: dict[str, Any]) -> list[int]:
    return list(c.get("times", [])) + list(c.get("quoted", []))


def _clock(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def comment_note(comments: list[dict[str, Any]]) -> str:
    """Text for the AI: the moments comments point at most, then the most-liked comments."""
    if not comments:
        return ""
    bins: dict[int, list[dict]] = {}
    for c in comments:
        for t in set(_points(c)):
            bins.setdefault(t // BIN_S, []).append(c)
    weight = lambda group: len(group) + sum(c["likes"] for c in group) / 100  # 100 likes count as one more comment
    ranked = sorted(bins.items(), key=lambda kv: -weight(kv[1]))[:MAX_BINS]
    parts = []
    if ranked:
        parts.append("Moments viewers point at in the comments, by timestamp or by quoting a line (strongest first):")
        for b, group in ranked:
            best = max(group, key=lambda c: c["likes"])
            likes = sum(c["likes"] for c in group)
            parts.append(f"- {_clock(b * BIN_S)}: {len(group)} comment(s), {likes} likes, e.g. \"{best['text'][:140]}\"")
    top = sorted((c for c in comments if not _points(c)), key=lambda c: -c["likes"])[:KEEP_TOP]
    if top:
        parts.append("Most-liked comments that name no time (find where the transcript covers the topic, line or "
                     "person they react to; a topic many of them raise is a strong moment):")
        parts += [f"- ({c['likes']} likes) {c['text'][:200]}" for c in top]
    return "\n".join(parts)


def mentions_in(comments: list[dict[str, Any]], start: float, end: float) -> int:
    return sum(1 for c in comments if any(start <= t <= end for t in _points(c)))
