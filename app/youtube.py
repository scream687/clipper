"""YouTube: video info, full-source download, and word-timed original captions.

`parse_json3_words` and the first version of `find_orig_caption_code` are adapted from
yt-short-clipper (https://github.com/jipraks/yt-short-clipper),
MIT License, Copyright (c) Aji Prakoso.
"""

import functools
import json
import os
import shutil
import threading
from pathlib import Path
from typing import Any, Callable

from app import FFMPEG, FFMPEG_BIN, ROOT, cookies_path

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


BROWSERS = ("chrome", "brave", "edge", "vivaldi", "opera", "firefox", "safari")  # tried in this order
SIGN_IN_DOMAINS = ("youtube.com", "google.com")  # only these cookies are copied, nothing else from the browser
SIGNED_IN = {"LOGIN_INFO", "SAPISID", "__Secure-3PSID"}  # any of these means a signed-in YouTube session
SIGN_IN_HELP = ("YouTube asks Clipper to sign in (\"confirm you're not a bot\"). Open youtube.com in Chrome, Brave, "
                "Edge, Firefox or Safari, make sure you are signed in, then press Try again. If your Mac asks to let "
                "Clipper use the browser's saved data, allow it.")
_cookie_lock = threading.Lock()


def is_bot_check(error: BaseException) -> bool:
    text = str(error)
    return "not a bot" in text or "Sign in to confirm" in text


def signed_in_jar(jar) -> list:
    """The YouTube and Google cookies of a browser's cookie jar, if they hold a signed-in session; else []. Pure."""
    kept = [c for c in jar if c.domain.lstrip(".").endswith(SIGN_IN_DOMAINS)]
    return kept if any(c.name in SIGNED_IN for c in kept) else []


def refresh_cookies(browsers: tuple[str, ...] = BROWSERS) -> str | None:
    """Copy the signed-in YouTube session from the first browser that has one into cookies.txt (only YouTube and
    Google cookies, readable only by you). Returns the browser used, or None. macOS may ask to allow access."""
    import http.cookiejar

    from yt_dlp.cookies import extract_cookies_from_browser

    class Quiet:
        def debug(self, *a, **k): pass
        info = warning = error = debug
        def progress_bar(self): return None  # noqa: E704

    with _cookie_lock:
        for browser in browsers:
            try:
                kept = signed_in_jar(extract_cookies_from_browser(browser, logger=Quiet()))
            except Exception:  # not installed, locked, or access refused: try the next one
                continue
            if not kept:
                continue
            target = ROOT / "cookies.txt"
            jar = http.cookiejar.MozillaCookieJar(str(target))
            for cookie in kept:
                jar.set_cookie(cookie)
            jar.save(ignore_discard=True, ignore_expires=True)
            os.chmod(target, 0o600)
            return browser
    return None


def signed_in(fn):
    """Run a yt-dlp call; if YouTube wants a sign-in, copy the session from the browser once and try again."""
    @functools.wraps(fn)
    def run(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as error:
            if not is_bot_check(error):
                raise
            if refresh_cookies() is None:
                raise RuntimeError(SIGN_IN_HELP) from error
            try:
                return fn(*args, **kwargs)
            except Exception as again:
                if is_bot_check(again):
                    raise RuntimeError(SIGN_IN_HELP) from again
                raise
    return run


@signed_in
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


@signed_in
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


@signed_in
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


def segments(playlist: str) -> dict[int, tuple[float, str]]:
    """Sequence number -> (seconds, url) of each segment in an HLS media playlist. Pure, unit-tested."""
    seq = 0
    for line in playlist.splitlines():
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            seq = int(line.split(":", 1)[1])
            break
    out, length = {}, None
    for line in playlist.splitlines():
        if line.startswith("#EXTINF:"):
            length = float(line.split(":", 1)[1].split(",")[0])
        elif line and not line.startswith("#") and length is not None:
            out[seq] = (length, line.strip())
            seq, length = seq + 1, None
    return out


def window_playlists(lists: list[dict[int, tuple[float, str]]], window_s: float) -> tuple[list[str], float]:
    """Fixed playlists for the last `window_s` seconds that every input has, starting on the same segment number,
    so separate video and audio start in step. Returns the playlists and the seconds they cover. Pure, unit-tested."""
    common = sorted(set.intersection(*(set(s) for s in lists)))
    if not common:
        raise RuntimeError("YouTube's video and audio of this stream have no segments in common yet; try again.")
    picked, total = [], 0.0
    for n in reversed(common):
        if total >= window_s or (picked and picked[-1] != n + 1):  # stop at the window or at a gap
            break
        picked.append(n)
        total += lists[0][n][0]
    picked.reverse()
    body = lambda s: "".join(f"#EXTINF:{s[n][0]:.3f},\n{s[n][1]}\n" for n in picked)
    head = f"#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-PLAYLIST-TYPE:VOD\n#EXT-X-TARGETDURATION:{int(max(lists[0][n][0] for n in picked)) + 1}\n#EXT-X-MEDIA-SEQUENCE:{picked[0]}\n"
    return [head + body(s) + "#EXT-X-ENDLIST\n" for s in lists], total


def download_live_window(info: dict[str, Any], out: Path, window_s: float, log: LogFn) -> Path:
    """The last `window_s` seconds of a stream that is still live, from YouTube's rewind buffer, without waiting for it
    to end. Video and audio come as separate playlists; both are cut to the same segment numbers (the live edge moves
    between two fetches, so "N segments back" alone can start them seconds apart and the lips stop matching)."""
    import subprocess
    import urllib.request
    urls = [u for u in live_manifest(info) if u]
    lists = []
    for url in urls:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=30) as r:  # noqa: S310
            lists.append(segments(r.read(20_000_000).decode("utf-8", "replace")))
    playlists, covered = window_playlists(lists, window_s)
    if covered < window_s - 10:
        log(f"Live stream: YouTube keeps only the last {round(covered / 60, 1)} minutes; capturing that")
    else:
        log(f"Live stream: capturing the last {round(covered / 60)} minutes")
    partial = out.with_suffix(".download.mp4")
    inputs = []
    for n, text in enumerate(playlists):
        local = out.with_name(f"window{n}.m3u8")
        local.write_text(text)
        inputs += ["-protocol_whitelist", "file,http,https,tcp,tls,crypto", "-i", str(local)]
    maps = ["-map", "0:v:0", "-map", "1:a:0"] if len(playlists) == 2 else []
    try:
        result = subprocess.run([FFMPEG, "-v", "error", "-y", *inputs, *maps, "-c", "copy", "-movflags", "+faststart",
                                 str(partial)], capture_output=True, text=True, timeout=covered + 900)
    finally:
        for n in range(len(playlists)):
            out.with_name(f"window{n}.m3u8").unlink(missing_ok=True)
    if result.returncode != 0 or not partial.is_file() or partial.stat().st_size == 0:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"Could not capture the live stream: {result.stderr.strip()[-300:]}")
    partial.replace(out)
    return out
