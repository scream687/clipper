"""YouTube: video info, full-source download, and word-timed original captions.

`parse_json3_words` and `find_orig_caption_code` are adapted from
yt-short-clipper (https://github.com/jipraks/yt-short-clipper),
MIT License, Copyright (c) Aji Prakoso.
"""

import json
import shutil
from pathlib import Path
from typing import Any, Callable

from app import FFMPEG_BIN, cookies_path

LogFn = Callable[[str], None]

# Prefer H.264 at up to 1080p: higher tiers are VP9/AV1 only and decode far slower.
FORMAT = ("bestvideo[height<=1080][vcodec^=avc1]+bestaudio[acodec^=mp4a]/"
          "bestvideo[height<=1080][vcodec^=avc1]+bestaudio/bestvideo[height<=1080]+bestaudio/best[height<=1080]/best")
MAX_WORD_S = 1.2       # a "word" longer than this is really a pause; clamp it
LAST_WORD_S = 0.5
SPACED_WORD_SHARE = 0.3  # over this share of "words" containing spaces means line-level captions, not word timing


def _options(**extra: Any) -> dict[str, Any]:
    opts: dict[str, Any] = {"quiet": True, "no_warnings": True, "noprogress": True,
                            "ffmpeg_location": str(FFMPEG_BIN), "remote_components": ["ejs:github"]}
    deno = shutil.which("deno")
    if deno:
        opts["js_runtimes"] = {"deno": {"path": deno}}
    if cookies_path():
        opts["cookiefile"] = cookies_path()
    return {**opts, **extra}


def fetch_info(url: str) -> dict[str, Any]:
    import yt_dlp

    with yt_dlp.YoutubeDL(_options(skip_download=True)) as ydl:
        return ydl.sanitize_info(ydl.extract_info(url, download=False))


def find_orig_caption_code(automatic_captions: dict[str, Any] | None) -> str | None:
    """The original-language auto-caption track (`*-orig`) is the only one with real per-word timing."""
    for code in (automatic_captions or {}):
        if code.endswith("-orig"):
            return code
    return None


def download_source(url: str, out: Path, log: LogFn) -> Path:
    import yt_dlp

    def progress(d: dict) -> None:
        if d.get("status") == "downloading" and d.get("_percent_str"):
            log(f"Downloading video {d['_percent_str'].strip()}")

    partial = out.with_suffix(".download.mp4")
    with yt_dlp.YoutubeDL(_options(format=FORMAT, merge_output_format="mp4", outtmpl=str(partial),
                                   progress_hooks=[progress], retries=5, fragment_retries=5)) as ydl:
        ydl.download([url])
    partial.replace(out)
    return out


def download_caption_words(url: str, code: str, work: Path) -> list[dict[str, Any]]:
    import yt_dlp

    work.mkdir(parents=True, exist_ok=True)
    with yt_dlp.YoutubeDL(_options(skip_download=True, writeautomaticsub=True, subtitleslangs=[code],
                                   subtitlesformat="json3", outtmpl=str(work / "captions.%(ext)s"))) as ydl:
        ydl.extract_info(url, download=True)
    files = list(work.glob("captions.*.json3"))
    if not files:
        raise RuntimeError(f"YouTube did not return the {code} caption track.")
    words = parse_json3_words(files[0])
    for f in files:
        f.unlink()
    return words


def parse_json3_words(path: Path) -> list[dict[str, Any]]:
    """json3 -> time-ordered [{"word", "start", "end"}] in source seconds."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    raw = []
    for event in data.get("events") or []:
        base = event.get("tStartMs", 0) or 0
        for seg in event.get("segs") or []:
            text = (seg.get("utf8") or "").strip()
            if text:
                raw.append((base + (seg.get("tOffsetMs") or 0), text))
    raw.sort(key=lambda item: item[0])
    words = []
    for i, (start_ms, text) in enumerate(raw):
        start = start_ms / 1000
        end = raw[i + 1][0] / 1000 if i + 1 < len(raw) else start + LAST_WORD_S
        if end <= start:
            end = start + LAST_WORD_S
        words.append({"word": text, "start": start, "end": min(end, start + MAX_WORD_S)})
    return words


def has_word_timing(words: list[dict[str, Any]]) -> bool:
    """Manual captions come one line per event, so most entries hold several words."""
    if not words:
        return False
    spaced = sum(1 for w in words if " " in w["word"])
    return spaced / len(words) <= SPACED_WORD_SHARE
