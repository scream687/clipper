"""Local web server for the clipper. Binds to 127.0.0.1 only (see run.sh)."""

import json
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator, model_validator

from app import DATA_DIR, ROOT, cleanup, library, queue
from app.model import Clip, CropKey, FramingMode, Look, Shape, Video
from app.ai import ask_claude
from app.emoji import is_emoji
from app.moments import find_clips
from app.pipeline import add_clips, analyse, create_from_upload, create_from_youtube
from app.plan import FONTS, build_plan
from app.render import render

SETTINGS_FILE = DATA_DIR / "settings.json"
WEB_DIR = ROOT / "web"
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
LOCAL_HOSTS = {"127.0.0.1", "localhost"}
FONT_FILES = {"arial-black.ttf": FONTS["Arial Black"], "arial-bold.ttf": FONTS["Arial Bold"]}
MAX_TEXT = 300

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; "
        "media-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-cache",
}

@asynccontextmanager
async def lifespan(_app: FastAPI):
    cleanup.resume_unfinished(_analyse_job)  # finish analyses a restart interrupted
    cleanup.start_cleanup_timer()
    yield


app = FastAPI(title="Clipper", docs_url=None, redoc_url=None, lifespan=lifespan)


def is_youtube_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and (parsed.hostname or "").lower() in YOUTUBE_HOSTS


@app.middleware("http")
async def local_requests_only(request: Request, call_next):
    """Refuse requests not addressed to this machine (DNS rebinding) or sent by another website."""
    host = (request.headers.get("host") or "").rsplit(":", 1)[0].lower()
    origin = request.headers.get("origin")
    origin_host = (urlparse(origin).hostname or "").lower() if origin else None
    if host not in LOCAL_HOSTS or (origin is not None and origin_host not in LOCAL_HOSTS):
        return JSONResponse({"detail": "This server only answers its own local page."}, status_code=403)
    response = await call_next(request)
    for header, value in SECURITY_HEADERS.items():
        response.headers.setdefault(header, value)
    return response


def _not_found(what: str):
    raise HTTPException(404, f"{what} not found")


def get_video_or_404(video_id: str) -> Video:
    try:
        return library.load_video(video_id)
    except KeyError:
        _not_found("Video")


def get_clip_or_404(video_id: str, index: int) -> Clip:
    try:
        return library.load_clip(video_id, index)
    except KeyError:
        _not_found("Clip")


# ---------- settings ----------

class SettingsIn(BaseModel):
    handle: str = Field(default="", max_length=60)


def load_settings() -> dict[str, Any]:
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as error:
        raise HTTPException(500, f"Could not read {SETTINGS_FILE.name}: {error}") from error


def save_settings(settings: dict[str, Any]) -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(SETTINGS_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(settings, handle, indent=2)


@app.get("/api/settings")
def get_settings() -> dict[str, Any]:
    return {"handle": load_settings().get("handle", "")}


@app.put("/api/settings")
def put_settings(body: SettingsIn) -> dict[str, Any]:
    save_settings({**load_settings(), "handle": body.handle.strip()})
    return get_settings()


# ---------- videos ----------

class NewVideo(BaseModel):
    url: str
    shape: Shape = "9:16"
    max_clips: int = Field(default=30, ge=1, le=50)
    min_s: int = Field(default=20, ge=5, le=300)
    max_s: int = Field(default=60, ge=10, le=600)
    direction: str = Field(default="", max_length=1000)

    @field_validator("url")
    @classmethod
    def url_is_youtube(cls, value: str) -> str:
        if not is_youtube_url(value.strip()):
            raise ValueError("Paste a YouTube link (youtube.com or youtu.be)")
        return value.strip()

    @model_validator(mode="after")
    def lengths_are_ordered(self) -> "NewVideo":
        if self.max_s <= self.min_s:
            raise ValueError("The longest clip length must be more than the shortest")
        return self


def _summary(video: Video) -> dict[str, Any]:
    clips = library.load_clips(video.id)
    job = queue.active_for(video.id)
    return {"id": video.id, "title": video.title, "channel": video.channel, "url": video.url,
            "duration": video.duration, "stage": video.stage, "error": video.error, "shape": video.shape,
            "created_at": video.created_at, "clip_count": len(clips),
            "kept": sum(1 for c in clips if c.status == "kept"), "job": job["id"] if job else None}


def _analyse_job(video_id: str):
    return lambda log: {"video_id": analyse(video_id, log).id}


@app.get("/api/videos")
def list_videos() -> list[dict[str, Any]]:
    return [_summary(v) for v in library.list_videos()]


@app.post("/api/videos")
def add_video(body: NewVideo) -> dict[str, Any]:
    video = create_from_youtube(body.url, {"max_clips": body.max_clips, "min_s": body.min_s, "max_s": body.max_s,
                                           "direction": body.direction.strip()}, body.shape)
    job = queue.submit("analysis", "analyse", video.id, _analyse_job(video.id))
    return {"id": video.id, "job": job}


UPLOAD_TYPES = {".mp4", ".mov", ".m4v", ".mkv", ".webm"}
UPLOAD_CHUNK = 8 * 1024 * 1024
LANGUAGES = {"hi": "Hindi or Hinglish", "en": "English"}


@app.post("/api/uploads")
def upload_video(file: UploadFile = File(...), language: str = Form("hi"), shape: Shape = Form("9:16"),
                 max_clips: int = Form(30), min_s: int = Form(20), max_s: int = Form(60), direction: str = Form("")) -> dict[str, Any]:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in UPLOAD_TYPES:
        raise HTTPException(422, "Upload an MP4, MOV, M4V, MKV or WebM video.")
    if language not in LANGUAGES:
        raise HTTPException(422, "Choose Hindi/Hinglish or English.")
    options = NewVideo(url="https://youtu.be/upload", shape=shape, max_clips=max_clips, min_s=min_s, max_s=max_s,
                       direction=direction)  # reuse the same limits as links
    video = create_from_upload(file.filename or "upload", {"max_clips": options.max_clips, "min_s": options.min_s,
                               "max_s": options.max_s, "direction": options.direction.strip(),
                               "upload_file": f"upload{suffix}"}, options.shape, language)
    target = library.video_dir(video.id) / f"upload{suffix}"
    with open(target, "wb") as out:  # streamed in chunks: hour-long uploads never sit in memory
        while chunk := file.file.read(UPLOAD_CHUNK):
            out.write(chunk)
    job = queue.submit("analysis", "analyse", video.id, _analyse_job(video.id))
    return {"id": video.id, "job": job}


@app.post("/api/videos/{video_id}/retry")
def retry_video(video_id: str) -> dict[str, Any]:
    video = get_video_or_404(video_id)
    if queue.active_for(video_id):
        raise HTTPException(409, "This video is already being worked on.")
    library.save_video(video.model_copy(update={"stage": "queued", "error": None}))
    return {"id": video_id, "job": queue.submit("analysis", "analyse", video_id, _analyse_job(video_id))}


@app.delete("/api/videos/{video_id}")
def delete_video(video_id: str) -> dict[str, str]:
    get_video_or_404(video_id)
    if queue.active_for(video_id):
        raise HTTPException(409, "Wait for the current job on this video to finish first.")
    library.delete_video(video_id)
    return {"deleted": video_id}


@app.get("/api/videos/{video_id}")
def get_video(video_id: str) -> dict[str, Any]:
    video = get_video_or_404(video_id)
    words = {w.id: w for w in video.words}
    clips = []
    for c in library.load_clips(video_id):
        first, last = words.get(c.first_word), words.get(c.last_word)
        clips.append({**c.model_dump(include={"index", "title", "hook", "description", "score", "retention", "mentions", "status",
                                              "look", "shape"}),
                      "start": first.start if first else 0, "end": last.end if last else 0,
                      "exported": (library.clip_dir(video_id, c.index) / "export.mp4").is_file()})
    zip_job = queue.active_for(f"{video_id}/zip")
    return {**_summary(video), "heatmap": video.heatmap, "language": video.language,
            "has_proxy": (library.video_dir(video_id) / "proxy.mp4").is_file(), "clips": clips,
            "zip_ready": (library.video_dir(video_id) / "clips.zip").is_file(), "zip_job": zip_job["id"] if zip_job else None}


@app.get("/api/videos/{video_id}/words")
def get_words(video_id: str) -> list[dict[str, Any]]:
    return [w.model_dump() for w in get_video_or_404(video_id).words]


@app.get("/api/videos/{video_id}/proxy.mp4")
def get_proxy(video_id: str) -> FileResponse:
    get_video_or_404(video_id)
    path = library.video_dir(video_id) / "proxy.mp4"
    if not path.is_file():
        _not_found("Preview copy")
    return FileResponse(path, media_type="video/mp4")


# ---------- clips ----------

class ClipEdit(BaseModel):
    first_word: int
    last_word: int
    cut_word_ids: list[int] = Field(default=[], max_length=5000)
    text_fixes: dict[int, str] = {}
    look: Look = "highlight"
    shape: Shape = "9:16"
    framing: FramingMode = "crop"
    manual_keys: list[CropKey] = Field(default=[], max_length=2000)
    hook: str = Field(default="", max_length=MAX_TEXT)
    show_hook: bool = True
    show_handle: bool = True
    show_credit: bool = True
    swear_filter: bool = False
    emoji_words: dict[int, str] = Field(default={}, max_length=50)
    show_emoji: bool = True
    title: str = Field(default="", max_length=MAX_TEXT)
    description: str = Field(default="", max_length=1000)
    status: str = Field(default="new", pattern="^(new|kept|discarded)$")

    @field_validator("emoji_words")
    @classmethod
    def emojis_are_emoji(cls, value: dict[int, str]) -> dict[int, str]:
        if not all(is_emoji(e) for e in value.values()):
            raise ValueError("Only single emoji can be added to a word")
        return value

    @field_validator("text_fixes")
    @classmethod
    def fixes_are_short(cls, value: dict[int, str]) -> dict[int, str]:
        if any(len(text) > 60 or not text.strip() for text in value.values()):
            raise ValueError("A corrected word must be 1-60 characters")
        return {k: v.strip() for k, v in value.items()}


def _plan_for(video: Video, clip: Clip) -> dict[str, Any]:
    return build_plan(video, clip, handle=load_settings().get("handle", "")).model_dump()


def _media(video: Video, clip: Clip) -> dict[str, Any]:
    """Where the preview plays from: the proxy while the full video exists, the kept window after cleanup."""
    if (library.video_dir(video.id) / "proxy.mp4").is_file():
        return {"url": f"/api/videos/{video.id}/proxy.mp4", "offset": 0.0, "window": None}
    if clip.mezz_start_frame is not None:
        return {"url": f"/api/videos/{video.id}/clips/{clip.index}/media.mp4", "offset": clip.mezz_start_frame / video.fps,
                "window": [clip.mezz_start_frame / video.fps, clip.mezz_end_frame / video.fps]}
    return {"url": None, "offset": 0.0, "window": None}


@app.get("/api/videos/{video_id}/clips/{index}")
def get_clip(video_id: str, index: int) -> dict[str, Any]:
    video, clip = get_video_or_404(video_id), get_clip_or_404(video_id, index)
    export = library.clip_dir(video_id, index) / "export.mp4"
    return {"clip": clip.model_dump(), "plan": _plan_for(video, clip), "exported": export.is_file(),
            "media": _media(video, clip), "job": (queue.active_for(f"{video_id}/{index}") or {}).get("id")}


@app.get("/api/videos/{video_id}/clips/{index}/media.mp4")
def get_clip_media(video_id: str, index: int) -> FileResponse:
    get_video_or_404(video_id); get_clip_or_404(video_id, index)
    path = library.clip_dir(video_id, index) / "mezz.mp4"
    if not path.is_file():
        _not_found("Clip video")
    return FileResponse(path, media_type="video/mp4")


@app.put("/api/videos/{video_id}/clips/{index}")
def edit_clip(video_id: str, index: int, body: ClipEdit) -> dict[str, Any]:
    video, clip = get_video_or_404(video_id), get_clip_or_404(video_id, index)
    ids = {w.id for w in video.words}
    if body.first_word not in ids or body.last_word not in ids or body.first_word > body.last_word:
        raise HTTPException(422, "The clip's start must be a word before its end.")
    inside = set(range(body.first_word, body.last_word + 1))
    if not set(body.cut_word_ids) <= inside or not set(body.text_fixes) <= inside or not set(body.emoji_words) <= inside:
        raise HTTPException(422, "Cuts and corrections must be inside the clip.")
    if set(range(body.first_word, body.last_word + 1)) <= set(body.cut_word_ids):
        raise HTTPException(422, "A clip needs at least one word that is not cut.")
    window = _media(video, clip)["window"]
    if window:
        words = {w.id: w for w in video.words}
        if words[body.first_word].start < window[0] or words[body.last_word].end > window[1]:
            raise HTTPException(422, "The full video was cleaned up after 14 days, so this clip can only be "
                                     "trimmed within the part that was kept.")
    updated = clip.model_copy(update=body.model_dump())
    library.save_clip(video_id, updated)
    return {"clip": updated.model_dump(), "plan": _plan_for(video, updated)}


EMOJI_SCHEMA = {"type": "object", "properties": {"emojis": {"type": "array", "maxItems": 8, "items": {
    "type": "object", "properties": {"word_id": {"type": "integer"}, "emoji": {"type": "string"}},
    "required": ["word_id", "emoji"], "additionalProperties": False}}}, "required": ["emojis"], "additionalProperties": False}


@app.post("/api/videos/{video_id}/clips/{index}/emojis")
def suggest_emojis(video_id: str, index: int) -> dict[str, Any]:
    video, clip = get_video_or_404(video_id), get_clip_or_404(video_id, index)
    cut = set(clip.cut_word_ids)
    ids = [w for w in range(clip.first_word, clip.last_word + 1) if w not in cut]
    words = {w.id: w for w in video.words}
    listing = " ".join(f"[{i}]{clip.text_fixes.get(i, words[i].display)}" for i in ids)
    prompt = ("This is the transcript of a short vertical video clip; each word has its id in brackets. Pick 3 to 6 "
              "words where an iOS emoji above the captions would add punch (money, health, shock, laughter, key nouns), "
              "spread across the clip and at least 3 seconds apart. Choose one fitting standard emoji for each.\n\n" + listing)
    try:
        answer = ask_claude(prompt, EMOJI_SCHEMA, "You are a short-form video editor. Return only the requested JSON.")
    except Exception as error:
        raise HTTPException(503, f"Claude could not suggest emojis right now: {str(error)[:200]}") from error
    picks = {e["word_id"]: e["emoji"].strip() for e in answer.get("emojis", [])
             if e.get("word_id") in ids and is_emoji(e.get("emoji", "").strip())}
    if not picks:
        raise HTTPException(502, "Claude did not return usable emojis. Try again.")
    updated = clip.model_copy(update={"emoji_words": picks, "show_emoji": True})
    library.save_clip(video_id, updated)
    return {"clip": updated.model_dump(), "plan": _plan_for(video, updated)}


@app.post("/api/videos/{video_id}/clips/{index}/export")
def export_clip(video_id: str, index: int) -> dict[str, str]:
    video, clip = get_video_or_404(video_id), get_clip_or_404(video_id, index)
    try:
        source, offset = library.media_for(video_id, clip)
    except FileNotFoundError as error:
        raise HTTPException(409, f"{error} This clip cannot be exported.") from error
    subject = f"{video_id}/{index}"
    if queue.active_for(subject):
        raise HTTPException(409, "This clip is already being exported.")
    plan = build_plan(video, clip, handle=load_settings().get("handle", ""))
    out = library.clip_dir(video_id, index) / "export.mp4"

    def work(log):
        render(plan, source, out, log, frame_offset=offset)
        library.save_clip(video_id, library.load_clip(video_id, index).model_copy(update={"status": "kept"}))
        return {"video_id": video_id, "index": index, "exported_at": datetime.now().isoformat(timespec="seconds")}

    return {"job": queue.submit("export", "export", subject, work)}


@app.get("/api/videos/{video_id}/clips/{index}/export.mp4")
def download_export(video_id: str, index: int, download: bool = False) -> FileResponse:
    video, clip = get_video_or_404(video_id), get_clip_or_404(video_id, index)
    path = library.clip_dir(video_id, index) / "export.mp4"
    if not path.is_file():
        _not_found("Export")
    name = f"{video.id}_{clip.index:02d}.mp4" if download else None
    return FileResponse(path, media_type="video/mp4", filename=name)


class FindIn(BaseModel):
    query: str = Field(min_length=3, max_length=500)


class NewClip(BaseModel):
    first_word: int
    last_word: int


@app.post("/api/videos/{video_id}/find")
def find_moment(video_id: str, body: FindIn) -> dict[str, str]:
    video = get_video_or_404(video_id)
    if video.stage != "ready":
        raise HTTPException(409, "Wait until this video has finished analysing.")
    o = video.options

    def work(log):
        log("Asking Claude for that moment")
        direction = (f"The user is looking for this specific moment: {body.query.strip()}. Return only moments that "
                     "match it, best match first; return none if nothing matches.")
        try:
            clips = find_clips(video, ask_claude, min_s=o.get("min_s", 20), max_s=o.get("max_s", 60), max_clips=3,
                               direction=direction)
        except RuntimeError as error:
            raise RuntimeError("Claude found nothing matching that description.") from error
        return {"added": add_clips(video, clips, log)}

    return {"job": queue.submit("analysis", "find", video_id, work)}


@app.post("/api/videos/{video_id}/clips")
def make_clip(video_id: str, body: NewClip) -> dict[str, Any]:
    video = get_video_or_404(video_id)
    ids = {w.id for w in video.words}
    if body.first_word not in ids or body.last_word not in ids or body.first_word > body.last_word:
        raise HTTPException(422, "The clip's start must be a word before its end.")
    if not (library.video_dir(video_id) / "proxy.mp4").exists():
        raise HTTPException(409, "The full video was cleaned up, so new clips cannot be made from it.")
    words = {w.id: w for w in video.words}
    title = " ".join(words[i].display for i in range(body.first_word, min(body.first_word + 8, body.last_word + 1)))
    index = add_clips(video, [Clip(index=0, first_word=body.first_word, last_word=body.last_word, title=title[:80])],
                      log=lambda m: None)[0]
    return {"index": index}


@app.post("/api/videos/{video_id}/export-all")
def export_all(video_id: str) -> dict[str, str]:
    get_video_or_404(video_id)
    subject = f"{video_id}/zip"
    if queue.active_for(subject):
        raise HTTPException(409, "This video's clips are already being exported.")
    handle = load_settings().get("handle", "")
    work = lambda log: {"video_id": video_id, "zip": cleanup.export_all(video_id, handle, log).name}
    return {"job": queue.submit("export", "export-all", subject, work)}


@app.get("/api/videos/{video_id}/clips.zip")
def download_zip(video_id: str) -> FileResponse:
    video = get_video_or_404(video_id)
    path = library.video_dir(video_id) / "clips.zip"
    if not path.is_file():
        _not_found("Zip")
    name = "".join(ch for ch in video.title if ch.isalnum() or ch in " -_").strip()[:60] or video.id
    return FileResponse(path, media_type="application/zip", filename=f"{name} clips.zip")


# ---------- jobs, fonts, page ----------

@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    job = queue.get(job_id)
    if job is None:
        _not_found("Job (the server may have restarted)")
    return job


@app.get("/fonts/{name}")
def get_font(name: str) -> FileResponse:
    if name not in FONT_FILES:
        _not_found("Font")
    return FileResponse(FONT_FILES[name], media_type="font/ttf")


if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
