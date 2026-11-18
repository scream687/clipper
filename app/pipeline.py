"""Analyse a video in resumable stages. Each stage skips itself when its output already exists,
so a restart picks up where it stopped."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Callable

from app import library
from app.ai import AIError, AIUnavailable, ask_ai
from app.house import classify, saved_look, with_look
from app.pacing import tighten
from app.edits import decorate
from app.loudness import loud_seconds, loudness_clips, measure as measure_loudness
from app.chat import fetch_replay_peaks
from app.comments import fetch_comments, keep_for_ai, match_quotes
from app.framing import WIDE_SHOT_TILES, WIDE_SHOT_WIDTH, auto_framing, find_facecam
from app.media import make_proxy, normalise_upload, probe, remove_stale_partials
from app.model import Clip, Video, Word
from app.moments import clips_at_booms, find_clips
from app.taste import taste_note
from app.trends import current_note
from app.transcribe import transcribe
from app.sentences import split_sentences
from app.translit import romanize_in_context
from app.youtube import (LIVE_WINDOW_S, download_caption_words, download_live_window, download_source, fetch_info,
                         find_orig_caption_code, has_word_timing)

LogFn = Callable[[str], None]
_BACKGROUND = ThreadPoolExecutor(max_workers=2)  # comment and chat reads run beside the download
CHAT_WAIT_S = 900  # a long stream's chat replay takes minutes to download; past this, clip without it
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
    """YouTube's original-language auto-captions (word-timed) win when they exist: on Hinglish podcasts they
    transcribe what was said, while Whisper turbo tends to translate into English. Uploads use Whisper."""
    code = video.options.get("caption_code")
    if video.url and code:
        raw = download_caption_words(video.url, code, folder / "work")
        if has_word_timing(raw):
            return raw
        log("YouTube only has line-level captions here; using Whisper instead")
    return transcribe(source, folder / "work", (video.language or "hi").split("-")[0], log,
                      vad=not video.options.get("live"))


def _window(video: Video, clip: Clip) -> tuple[float, float]:
    by_id = {w.id: w for w in video.words}
    return max(0.0, by_id[clip.first_word].start - FRAMING_MARGIN_S), by_id[clip.last_word].end + FRAMING_MARGIN_S


def look_for_facecam(video: Video, clip: Clip) -> Clip:
    """Find the clip's facecam once ([] remembers there is none). Needs the preview copy, which cleanup removes."""
    proxy = library.video_dir(video.id) / "proxy.mp4"
    if clip.cam_box is not None or not proxy.is_file():
        return clip
    return clip.model_copy(update={"cam_box": find_facecam(proxy, *_window(video, clip)) or []})


GAME_LIKE = ("gameplay", "stream")  # faces in these are game characters or a corner facecam, not a shot to follow


def frame_clip(video: Video, clip: Clip) -> Clip:
    """Pick the framing by content type: a game or stream with a facecam is split (cam on top, game below);
    a game without one shows the whole frame; everything else follows the speaker's face."""
    vertical = clip.shape != "16:9"
    if video.content in GAME_LIKE and vertical:
        framed = look_for_facecam(video, clip.model_copy(update={"framed": True}))
        if framed.cam_box:
            framed = framed.model_copy(update={"framing": "split", "auto_keys": []})
            library.save_clip(video.id, framed)
            return framed
        if video.content == "gameplay":
            framed = framed.model_copy(update={"framing": "fill", "auto_keys": []})
            library.save_clip(video.id, framed)
            return framed
    proxy = library.video_dir(video.id) / "proxy.mp4"
    framing, keys = auto_framing(proxy, *_window(video, clip))
    if framing == "fill" and video.content in ("show", "vlog"):  # small faces on a stage or far from the camera
        framing, keys = auto_framing(proxy, *_window(video, clip), width=WIDE_SHOT_WIDTH, tiles=WIDE_SHOT_TILES)
    framed = clip.model_copy(update={"framing": framing, "auto_keys": keys, "framed": True})
    # No face to follow: a screen recording may have a facecam. Talks, shows and vlogs never do (a small face on a
    # stage is a person in a wide shot, not an overlay).
    if framing == "fill" and vertical and video.content not in ("talk", "show", "vlog"):
        framed = look_for_facecam(video, framed)
        if framed.cam_box:
            framed = framed.model_copy(update={"framing": "split"})
    library.save_clip(video.id, framed)
    return framed


_index_lock = threading.Lock()  # two jobs adding clips at once (OpusClip + Ask AI) must not share an index


def add_clips(video: Video, clips: list[Clip], log: LogFn) -> list[int]:
    """Append clips after the existing ones and frame them. Returns their indices."""
    with _index_lock:  # ponytail: one process-wide lock; per-video locks if adding clips ever gets busy
        start = max((c.index for c in library.load_clips(video.id)), default=-1) + 1
        look = saved_look()
        placed = [tighten(video, with_look(clip.model_copy(update={"index": start + n, "shape": video.shape}), look))
                  for n, clip in enumerate(clips)]
        for clip in placed:
            library.save_clip(video.id, clip)
    placed = decorate(video, placed, ask_ai, log)
    for clip in placed:
        library.save_clip(video.id, clip)
    added = []
    for clip in placed:
        if (library.video_dir(video.id) / "proxy.mp4").exists():
            frame_clip(video, clip)
        added.append(clip.index)
        log(f"Added clip {clip.index + 1}: {clip.title}")
    return added


def _match_comments(video: Video, reading, comments_file: Path, log: LogFn) -> Video:
    """Wait for the background comment read, keep the raw list on disk, and give the video the comments
    that point at a moment (timestamp or quoted line) plus the most-liked rest."""
    try:
        if reading is not None:
            comments = reading.result()
            comments_file.write_text(json.dumps(comments, ensure_ascii=False), encoding="utf-8")
        else:
            comments = json.loads(comments_file.read_text(encoding="utf-8"))
        comments = match_quotes(comments, video.words)
        keep = keep_for_ai(comments)
        stamped = sum(1 for c in comments if c["times"])
        quoted = sum(1 for c in comments if c.get("quoted"))
        log(f"Read {len(comments)} comments: {stamped} give a timestamp, {quoted} quote a line")
    except Exception as error:  # comments off or blocked: carry on without them
        log(f"Could not read comments ({str(error)[:120]}); continuing without them")
        keep = []
    video = video.model_copy(update={"comments": keep, "options": {**video.options, "comments_read": True}})
    library.save_video(video)
    return video


def analyse(video_id: str, log: LogFn) -> Video:
    video = library.load_video(video_id)
    folder = library.video_dir(video_id)
    remove_stale_partials(folder)  # left by a server that was killed mid-stage
    try:
        if video.url and not video.channel:
            video = _stage(video, "Reading video details", log)
            info = fetch_info(video.url)
            live = bool(info.get("is_live"))
            code = None if live else find_orig_caption_code(info)  # a running stream has no captions yet: Whisper
            title = info.get("title") or video.url
            if live:
                title = f"{title} (live, last {round(video.options.get('live_s', LIVE_WINDOW_S) / 60)} min to {datetime.now():%H:%M})"
            video = video.model_copy(update={
                "title": title, "channel": info.get("channel") or info.get("uploader") or "",
                "duration": float(info.get("duration") or 0), "heatmap": list(info.get("heatmap") or []),
                # a running stream has no language from YouTube; Whisper in English mode would translate a Hindi
                # streamer instead of writing down what they say, and Hindi mode keeps their English lines as said
                "language": (code or info.get("language") or ("hi" if live else "en")).split("-")[0],
                "options": {**video.options, "caption_code": code, "live": live, "comments_read": live or video.options.get("comments_read", False),
                            # a finished stream keeps its chat as a replay; a running one only has chat from now on (watch.py)
                            "chat_read": live or "live_chat" not in (info.get("subtitles") or {})}})
            library.save_video(video)
            if live and video.options.get("watch_chat"):  # clips come from the chat's booms (watch.py), not the backlog
                log("Live stream: clips are made each time the chat booms; nothing to clip from the backlog")
                return _stage(video, "ready", log)
            if live:  # captured now, while it runs: the window ends at this moment
                video = _stage(video, "Capturing the live stream", log)
                download_live_window(info, folder / "source.mp4", video.options.get("live_s", LIVE_WINDOW_S), log)

        comments_file = folder / "comments.json"
        reading = None
        if video.url and not video.options.get("comments_read") and not comments_file.exists():
            log("Reading comments in the background")
            reading = _BACKGROUND.submit(fetch_comments, video.url, video.duration)

        chat_reading = None
        if video.url and video.options.get("chat_read") is False:
            log("Reading the live chat replay in the background")
            chat_reading = _BACKGROUND.submit(fetch_replay_peaks, video.url, folder / "work")

        source = folder / "source.mp4"
        if not source.exists() and video.options.get("watched_from"):  # a hype capture is of one moment, now gone
            raise RuntimeError("This chat-hype capture was interrupted, and that moment of the stream is gone. Delete it.")
        if not source.exists():
            if video.url:
                video = _stage(video, "Downloading video", log)
                info = fetch_info(video.url)  # checked here too: a resumed job skips the details step above
                if info.get("is_live"):
                    video = _stage(video, "Capturing the live stream", log)
                    download_live_window(info, source, video.options.get("live_s", LIVE_WINDOW_S), log)
                    video = video.model_copy(update={"options": {**video.options, "live": True, "comments_read": True,
                                                                  "caption_code": None}})
                    library.save_video(video)
                else:
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
            words = [Word(id=i, text=w["word"], display=w["word"], start=w["start"], end=w["end"]) for i, w in enumerate(raw)]
            sentences = split_sentences(words)
            romans = romanize_in_context([[w.text for w in s] for s in sentences], ask_ai, log)
            display = {w.id: r for s, rs in zip(sentences, romans) for w, r in zip(s, rs)}
            words = [w.model_copy(update={"display": display[w.id]}) for w in words]
            video = video.model_copy(update={"words": words})
            library.save_video(video)

        if reading is not None or (comments_file.exists() and not video.options.get("comments_read")):
            video = _stage(video, "Matching comments to the transcript", log)
            video = _match_comments(video, reading, comments_file, log)

        if chat_reading is not None:
            video = _stage(video, "Reading the chat replay", log)
            try:
                peaks = chat_reading.result(timeout=CHAT_WAIT_S)
                log(f"The chat spiked {len(peaks)} times" if peaks else "The chat never spiked")
            except Exception as error:  # chat off, removed or too slow: carry on with the other signals
                log(f"Could not read the chat ({str(error)[:120]}); continuing without it")
                peaks = []
            video = video.model_copy(update={"chat_peaks": peaks, "options": {**video.options, "chat_read": True}})
            library.save_video(video)

        if not video.options.get("loudness_measured"):
            video = _stage(video, "Measuring loudness", log)
            loud = loud_seconds(measure_loudness(source))
            video = video.model_copy(update={"loud": loud, "options": {**video.options, "loudness_measured": True}})
            library.save_video(video)
            log(f"{len(loud)} loud seconds")

        if not video.options.get("content_checked"):
            video = _stage(video, "Working out what kind of video this is", log)
            try:
                content = classify(video, ask_ai)
            except (AIUnavailable, AIError) as error:
                log(f"Could not tell the kind of video ({str(error)[:120]}); framing by faces alone")
                content = ""
            video = video.model_copy(update={"content": content, "options": {**video.options, "content_checked": True}})
            library.save_video(video)
            if content:
                log(f"This looks like a {content} video")

        if not library.load_clips(video_id):
            video = _stage(video, "Finding moments with AI", log)
            o = video.options
            try:
                if video.options.get("watched_from"):  # a chat-boom capture: the boom decides the clip, the AI names it
                    clips = clips_at_booms(video, ask_ai, o.get("max_s", 60), log)
                    if not clips:
                        raise RuntimeError("Nobody speaks around this chat boom, so there is nothing to caption. Delete it.")
                else:
                    clips = find_clips(video, ask_ai, min_s=o.get("min_s", 20), max_s=o.get("max_s", 60),
                                       max_clips=o.get("max_clips", 30), direction=o.get("direction", ""),
                                       trends=current_note(), taste=taste_note(video.id))
            except AIUnavailable as error:
                log(f"The AI is unavailable ({str(error)[:120]}); picking clips by loudness instead")
                clips = loudness_clips(video, o.get("min_s", 20), o.get("max_s", 60), o.get("max_clips", 30))
                if not clips:
                    raise RuntimeError("The AI is unavailable and this video has no loud moments to fall back on. "
                                       "Try again later.") from error
            look = saved_look()
            clips = decorate(video, [tighten(video, with_look(clip, look)) for clip in clips], ask_ai, log)
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
