"""YouTube: video info, full-source download, and word-timed original captions.

`parse_json3_words` and the first version of `find_orig_caption_code` are adapted from
yt-short-clipper (https://github.com/jipraks/yt-short-clipper),
MIT License, Copyright (c) Aji Prakoso.
"""

import json
import shutil
from pathlib import Path
from typing import Any, Callable

from app import FFMPEG, FFMPEG_BIN, cookies_path

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


def original_language(info: dict[str, Any]) -> str:
    """The language the video was made in: YouTube's own field, else the audio track marked "original"."""
    if info.get("language"):
        return str(info["language"]).lower()
    for f in info.get("formats") or []:
        if f.get("language") and "original" in (f.get("format_note") or "").lower():
            return str(f["language"]).lower()
    return ""


def find_orig_caption_code(info: dict[str, Any]) -> str | None:
    """The original-language auto-caption track (`*-orig`) is the only one with real per-word timing.

    A video with dubbed audio has one `-orig` track per dub (ar-orig, bn-orig, en-orig ...), so the track
    must match the video's own language; the first one listed is usually a dub."""
    origs = [c for c in (info.get("automatic_captions") or {}) if c.endswith("-orig")]
    lang = original_language(info)
    base = lang.split("-")[0]
    for code in origs:
        if code[:-5].lower() == lang:
            return code
    for code in origs:
        if code[:-5].lower().split("-")[0] == base:
            return code
    return origs[0] if origs else None


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


def download_caption_words(url: str, code: str | None, work: Path) -> list[dict[str, Any]]:
    """Word-timed captions of one track. `code=None` reads the video's info first to find its original track."""
    import yt_dlp

    if code is None:
        code = find_orig_caption_code(fetch_info(url))
        if code is None:
            raise RuntimeError("This video has no original-language auto-captions.")
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


LIVE_WINDOW_S = 30 * 60


def live_manifest(info: dict[str, Any]) -> tuple[str, str | None]:
    """HLS playlists of a running live stream (yt-dlp `info`): the best video up to 1080p, and the best audio-only
    playlist when YouTube serves sound separately (None when the video playlist carries sound)."""
    hls = [f for f in info.get("formats", []) if "m3u8" in (f.get("protocol") or "") and f.get("url")]
    has = lambda f, key: f.get(key) not in (None, "none")
    best = lambda fs: max(fs, key=lambda f: (f.get("height") or 0, f.get("tbr") or 0, f.get("format_id") or ""))
    muxed = [f for f in hls if has(f, "vcodec") and has(f, "acodec") and (f.get("height") or 0) <= 1080]
    if muxed:
        return best(muxed)["url"], None
    video = [f for f in hls if has(f, "vcodec") and (f.get("height") or 0) <= 1080]
    audio = [f for f in hls if not has(f, "vcodec") and not f.get("height")]
    if not video or not audio:
        raise RuntimeError("YouTube gave no playable live format for this stream.")
    return best(video)["url"], best(audio)["url"]


def target_duration(playlist: str) -> float:
    """#EXT-X-TARGETDURATION of an HLS media playlist (seconds per segment). Pure, unit-tested."""
    for line in playlist.splitlines():
        if line.startswith("#EXT-X-TARGETDURATION:"):
            return float(line.split(":", 1)[1])
    return 5.0


def download_live_window(info: dict[str, Any], out: Path, window_s: float, log: LogFn) -> Path:
    """The last `window_s` seconds of a stream that is still live, from YouTube's rewind buffer, without waiting for it
    to end: start that many segments back from the live edge and stop after `window_s` of media."""
    import math
    import subprocess
    import urllib.request
    video_url, audio_url = live_manifest(info)
    with urllib.request.urlopen(urllib.request.Request(video_url, headers={"User-Agent": "Mozilla/5.0"}), timeout=30) as r:  # noqa: S310
        segment_s = target_duration(r.read(2_000_000).decode("utf-8", "replace"))
    back = str(-math.ceil(window_s / segment_s))  # both playlists number segments the same, so they start in step
    log(f"Live stream: capturing the last {round(window_s / 60)} minutes")
    partial = out.with_suffix(".download.mp4")
    inputs = ["-live_start_index", back, "-i", video_url]
    if audio_url:
        inputs += ["-live_start_index", back, "-i", audio_url]
    maps = ["-map", "0:v:0", "-map", "1:a:0"] if audio_url else []
    result = subprocess.run([FFMPEG, "-v", "error", "-y", *inputs, *maps, "-t", f"{window_s:.0f}",
                             "-c", "copy", "-movflags", "+faststart", str(partial)],
                            capture_output=True, text=True, timeout=window_s + 900)
    if result.returncode != 0 or not partial.is_file() or partial.stat().st_size == 0:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"Could not capture the live stream: {result.stderr.strip()[-300:]}")
    partial.replace(out)
    return out
