"""Analyse a video in resumable stages. Each stage skips itself when its output already exists,
so a restart picks up where it stopped."""

from datetime import datetime
from pathlib import Path
from typing import Callable

from app import library
from app.ai import ask_claude
from app.framing import auto_framing
from app.media import make_proxy, normalise_upload, probe, remove_stale_partials
from app.model import Clip, Video, Word
from app.moments import find_clips
from app.transcribe import transcribe
from app.translit import romanize
from app.youtube import download_caption_words, download_source, fetch_info, find_orig_caption_code, has_word_timing

LogFn = Callable[[str], None]
FRAMING_MARGIN_S = 10.0  # frame a little beyond the clip so trimming outward still has face data


def create_from_youtube(url: str, options: dict, shape: str) -> Video:
    video = Video(id=library.new_id(url.rsplit("=", 1)[-1].rsplit("/", 1)[-1]), title=url, url=url,
                  duration=0, fps=0, width=0, height=0, shape=shape, options=options,
                  created_at=datetime.now().isoformat(timespec="seconds"))
    library.save_video(video)
    return video


def create_from_upload(filename: str, options: dict, shape: str, language: str) -> Video:
    stem = Path(filename).stem
    video = Video(id=library.new_id(stem), title=stem[:200] or "Uploaded video", duration=0, fps=0, width=0, height=0,
                  shape=shape, language=language, options=options, created_at=datetime.now().isoformat(timespec="seconds"))
    library.save_video(video)
    return video


def _stage(video: Video, name: str, log: LogFn) -> Video:
    video = video.model_copy(update={"stage": name, "error": None})
    library.save_video(video)
    log(name)
    return video


def _transcript(video: Video, source, folder, log: LogFn) -> list[dict]:
    """English YouTube videos with word-timed captions use them; everything else goes through Whisper."""
    code = video.options.get("caption_code")
    if video.url and code and code.startswith("en"):
        raw = download_caption_words(video.url, code, folder / "work")
        if has_word_timing(raw):
            return raw
        log("YouTube only has line-level captions here; using Whisper instead")
    return transcribe(source, folder / "work", (video.language or "hi").split("-")[0], log)


def frame_clip(video: Video, clip: Clip) -> Clip:
    by_id = {w.id: w for w in video.words}
    start = max(0.0, by_id[clip.first_word].start - FRAMING_MARGIN_S)
    end = by_id[clip.last_word].end + FRAMING_MARGIN_S
    framing, keys = auto_framing(library.video_dir(video.id) / "proxy.mp4", start, end)
    framed = clip.model_copy(update={"framing": framing, "auto_keys": keys, "framed": True})
    library.save_clip(video.id, framed)
    return framed


def add_clips(video: Video, clips: list[Clip], log: LogFn) -> list[int]:
    """Append clips after the existing ones and frame them. Returns their indices."""
    start = max((c.index for c in library.load_clips(video.id)), default=-1) + 1
    added = []
    for offset, clip in enumerate(clips):
        clip = clip.model_copy(update={"index": start + offset, "shape": video.shape})
        library.save_clip(video.id, clip)
        if (library.video_dir(video.id) / "proxy.mp4").exists():
            frame_clip(video, clip)
        added.append(clip.index)
        log(f"Added clip {clip.index + 1}: {clip.title}")
    return added


def analyse(video_id: str, log: LogFn) -> Video:
    video = library.load_video(video_id)
    folder = library.video_dir(video_id)
    remove_stale_partials(folder)  # left by a server that was killed mid-stage
    try:
        if video.url and not video.channel:
            video = _stage(video, "Reading video details", log)
            info = fetch_info(video.url)
            code = find_orig_caption_code(info.get("automatic_captions"))
            video = video.model_copy(update={
                "title": info.get("title") or video.url, "channel": info.get("channel") or info.get("uploader") or "",
                "duration": float(info.get("duration") or 0), "heatmap": list(info.get("heatmap") or []),
                "language": (code or info.get("language") or "en").split("-")[0],
                "options": {**video.options, "caption_code": code}})
            library.save_video(video)

        source = folder / "source.mp4"
        if not source.exists():
            if video.url:
                video = _stage(video, "Downloading video", log)
                download_source(video.url, source, log)
            else:
                video = _stage(video, "Preparing the uploaded video", log)
                upload = folder / video.options["upload_file"]
                normalise_upload(upload, source)
                upload.unlink(missing_ok=True)
        if not video.fps:
            facts = probe(source)
            video = video.model_copy(update=facts if video.url else {**facts, "duration": facts["duration"]})
            library.save_video(video)

        proxy = folder / "proxy.mp4"
        if not proxy.exists():
            video = _stage(video, "Making the preview copy", log)
            make_proxy(source, proxy)

        if not video.words:
            video = _stage(video, "Reading the transcript", log)
            raw = _transcript(video, source, folder, log)
            romans = romanize([w["word"] for w in raw], ask_claude, log)
            words = [Word(id=i, text=w["word"], display=romans.get(w["word"], w["word"]), start=w["start"], end=w["end"])
                     for i, w in enumerate(raw)]
            video = video.model_copy(update={"words": words})
            library.save_video(video)

        if not library.load_clips(video_id):
            video = _stage(video, "Finding moments with Claude", log)
            o = video.options
            clips = find_clips(video, ask_claude, min_s=o.get("min_s", 20), max_s=o.get("max_s", 60),
                               max_clips=o.get("max_clips", 30), direction=o.get("direction", ""))
            for clip in clips:
                library.save_clip(video_id, clip)
            log(f"Found {len(clips)} moments")

        unframed = [c for c in library.load_clips(video_id) if not c.framed]
        if unframed:
            video = _stage(video, "Framing clips", log)
            for number, clip in enumerate(unframed, 1):
                frame_clip(video, clip)
                log(f"Framed clip {number}/{len(unframed)}")

        return _stage(video, "ready", log)
    except Exception as error:
        failed = library.load_video(video_id).model_copy(update={"stage": "failed", "error": str(error)[:500]})
        library.save_video(failed)
        raise
