"""Local web server for the clipper. Binds to 127.0.0.1 only (see run.sh)."""

import io
import json
import os
import subprocess
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from app import FFMPEG, ROOT, SETTINGS_FILE, ai, assets, channels, cleanup, house, library, opusclip, queue, sfx, trends, watch
from app.model import HEX_COLOUR, Background, Clip, CropKey, FramingMode, Look, Shape, UnitFloat, Video
from app.emoji import is_emoji
from app.moments import clean_hashtags, find_clips
from app.pipeline import add_clips, analyse, create_from_upload, create_from_youtube, look_for_facecam
from app.plan import FONTS, build_plan

WEB_DIR = ROOT / "web"
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
LOCAL_HOSTS = {"127.0.0.1", "localhost"}
FONT_FILES = {"arial-black.ttf": FONTS["Arial Black"], "arial-bold.ttf": FONTS["Arial Bold"]}
MAX_TEXT = 300

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; "
        "media-src 'self'; connect-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",  # the editor frames its own text layer (web/overlay/)
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-cache",
}

@asynccontextmanager
async def lifespan(_app: FastAPI):
    cleanup.resume_unfinished(_analyse_job)  # finish analyses a restart interrupted
    for video_id in watch.to_resume():  # keep watching streams' chat across restarts (ends at once if one is over)
        _start_watch(video_id)
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
    brand: str = Field(default="", max_length=50)  # display name on the X post template
    channels: list[str] | None = Field(default=None, max_length=50)
    opusclip_key: str = Field(default="", max_length=500)  # blank keeps the saved one
    pexels_key: str = Field(default="", max_length=200)    # blank keeps the saved one
    upcoming: list[str] | None = Field(default=None, max_length=20)        # "2026-11-19 GTA 6 / GTA VI"
    trend_creators: list[str] | None = Field(default=None, max_length=60)
    export_quality: Literal["1080", "4k"] = "1080"
    export_fps: Literal["source", "30", "60"] = "source"


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
    settings = load_settings()
    upcoming, creators = trends.settings()
    return {"handle": settings.get("handle", ""), "brand": settings.get("brand", ""), "channels": settings.get("channels", channels.DEFAULT_CHANNELS),
            "has_opusclip_key": bool(settings.get("opusclip_key")), "has_pexels_key": bool(settings.get("pexels_key")),
            "upcoming": [f"{e['date']} {' / '.join(e['terms'])}" for e in upcoming], "trend_creators": creators,
            "export_quality": settings.get("export_quality", "1080"), "export_fps": settings.get("export_fps", "source")}


@app.put("/api/settings")
def put_settings(body: SettingsIn) -> dict[str, Any]:
    update = {"handle": "".join(body.handle.split()), "brand": body.brand.strip(), "export_quality": body.export_quality, "export_fps": body.export_fps}  # a handle has no spaces: "@  name" -> "@name"
    if body.channels is not None:
        update["channels"] = channels.clean_channels(body.channels)
    if body.upcoming is not None:
        update["upcoming"] = trends.parse_upcoming(body.upcoming)
    if body.trend_creators is not None:
        update["trend_creators"] = channels.clean_channels(body.trend_creators, limit=trends.MAX_CREATORS)
    if body.opusclip_key.strip():
        update["opusclip_key"] = body.opusclip_key.strip()
    if body.pexels_key.strip():
        update["pexels_key"] = body.pexels_key.strip()
    save_settings({**load_settings(), **update})
    return get_settings()


class AIIn(BaseModel):
    provider: str
    base_url: str = Field(default="", max_length=300)
    api_key: str = Field(default="", max_length=500)
    model: str = Field(default="", max_length=200)

    @field_validator("provider")
    @classmethod
    def known_provider(cls, value: str) -> str:
        if value not in ai.PROVIDERS:
            raise ValueError("Unknown AI provider.")
        return value


def ai_base_url(body: AIIn) -> str:
    """Preset providers keep their own address; only Ollama and custom APIs take one from the page."""
    url = body.base_url.strip() if body.provider in ai.EDITABLE_URL else ai.PROVIDERS[body.provider]["base_url"]
    if body.provider != ai.CLAUDE_CODE and (urlparse(url).scheme not in ("http", "https") or not urlparse(url).hostname):
        raise HTTPException(422, "The API address must be a full http:// or https:// URL.")
    return url


class LookFrom(BaseModel):
    video_id: str
    index: int


@app.put("/api/house-look")
def save_house_look(body: LookFrom) -> dict[str, Any]:
    """Make this clip's look the one every new clip starts with."""
    get_video_or_404(body.video_id)
    look = house.look_of(get_clip_or_404(body.video_id, body.index))
    save_settings({**load_settings(), "house_look": look})
    return look


@app.post("/api/videos/{video_id}/apply-look")
def apply_look(video_id: str, body: LookFrom) -> dict[str, int]:
    """Copy one clip's look to every other clip of the video."""
    get_video_or_404(video_id)
    look = house.look_of(get_clip_or_404(video_id, body.index))
    others = [c for c in library.load_clips(video_id) if c.index != body.index]
    for clip in others:
        library.save_clip(video_id, house.with_look(clip, look))
    return {"updated": len(others)}


@app.get("/api/ai")
def get_ai() -> dict[str, Any]:
    """The picked provider and model. Keys never leave the server; `key_urls` says which addresses have one."""
    config = load_settings().get("ai", {})
    return {"provider": config.get("provider", ai.CLAUDE_CODE), "model": config.get("model", ai.CLAUDE_MODEL),
            "base_url": config.get("base_url", ""), "key_urls": sorted(config.get("keys", {})),
            "providers": [{"id": key, **value} for key, value in ai.PROVIDERS.items()]}


@app.put("/api/ai")
def put_ai(body: AIIn) -> dict[str, Any]:
    url = ai_base_url(body)
    model = body.model.strip() or (ai.CLAUDE_MODEL if body.provider == ai.CLAUDE_CODE else "")
    if not model:
        raise HTTPException(422, "Pick a model first.")
    settings = load_settings()
    keys = settings.get("ai", {}).get("keys", {})
    if body.api_key.strip():
        keys = {**keys, url: body.api_key.strip()}
    save_settings({**settings, "ai": {"provider": body.provider, "model": model, "base_url": url, "keys": keys}})
    return get_ai()


@app.post("/api/ai/models")
def ai_models(body: AIIn) -> list[dict[str, Any]]:
    """Ask the provider which models it offers. Uses the typed key, else the one saved for this address."""
    url = ai_base_url(body)
    key = body.api_key.strip() or load_settings().get("ai", {}).get("keys", {}).get(url, "")
    try:
        return ai.list_models(body.provider, url, key)
    except ai.AIUnavailable as error:
        raise HTTPException(502, str(error)) from error


@app.get("/api/channels")
def watched_channels() -> list[dict[str, Any]]:
    """Latest uploads of the watched channels; `added` marks the ones already in the library."""
    added = _library_ids()
    result = channels.latest_for(get_settings()["channels"])
    return [{**c, "videos": [{**v, "added": v["id"] in added} for v in c["videos"]]} for c in result]


def _library_ids() -> set[str]:
    videos = library.list_videos()
    ids = {v.url.split("v=", 1)[-1].split("&", 1)[0] for v in videos if "v=" in v.url}
    return ids | {v.url.rsplit("/", 1)[-1].split("?", 1)[0] for v in videos if "youtu.be/" in v.url}


@app.get("/api/trends")
def trending() -> dict[str, Any]:
    """Upcoming events, India's top searches, and creator uploads beating their usual views."""
    upcoming, creators = trends.settings()
    added = _library_ids()
    return {"upcoming": trends.upcoming_now(upcoming), "searches": trends.searches()[:8],
            "videos": [{**v, "added": v["id"] in added} for v in trends.videos(creators)]}


# ---------- videos ----------

class NewVideo(BaseModel):
    url: str
    shape: Shape = "9:16"
    max_clips: int = Field(default=30, ge=1, le=50)
    min_s: int = Field(default=20, ge=5, le=300)
    max_s: int = Field(default=60, ge=10, le=600)
    direction: str = Field(default="", max_length=1000)
    live_minutes: int = Field(default=30, ge=5, le=240)  # live streams: how far back to capture from now
    watch_chat: bool = True  # live streams: keep watching the chat and clip every moment it explodes

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
            "kept": sum(1 for c in clips if c.status == "kept"), "job": job["id"] if job else None,
            "live": bool(video.options.get("live")), "watching": _watch_status(video.id)}


def _watch_status(video_id: str) -> str | None:
    """The watcher's latest line while it follows this stream's chat, else None."""
    job = queue.active_for(f"{video_id}/watch")
    return (job["log"][-1] if job["log"] else "Starting to watch the chat") if job else None


def _start_watch(video_id: str) -> str:
    on_capture = lambda child_id: queue.submit("analysis", "analyse", child_id, _analyse_job(child_id))
    return queue.submit(f"watch-{video_id}", "watch", f"{video_id}/watch",
                        lambda log: watch.watch(video_id, on_capture, log))


def _analyse_job(video_id: str):
    return lambda log: {"video_id": analyse(video_id, log).id}


@app.get("/api/videos")
def list_videos() -> list[dict[str, Any]]:
    return [_summary(v) for v in library.list_videos()]


@app.post("/api/videos")
def add_video(body: NewVideo) -> dict[str, Any]:
    video = create_from_youtube(body.url, {"max_clips": body.max_clips, "min_s": body.min_s, "max_s": body.max_s,
                                           "direction": body.direction.strip(), "live_s": body.live_minutes * 60,
                                           "watch_chat": body.watch_chat}, body.shape)
    job = queue.submit("analysis", "analyse", video.id, _analyse_job(video.id))
    if body.watch_chat:  # ends at once (with a note) when the link is not a running stream
        _start_watch(video.id)
    return {"id": video.id, "job": job}


@app.post("/api/videos/{video_id}/watch")
def start_watch(video_id: str) -> dict[str, Any]:
    video = get_video_or_404(video_id)
    if not video.url:
        raise HTTPException(422, "Only YouTube streams have a live chat to watch.")
    if _watch_status(video_id) is not None:
        raise HTTPException(409, "Already watching this stream's chat.")
    return {"job": _start_watch(video_id)}


@app.delete("/api/videos/{video_id}/watch")
def stop_watch(video_id: str) -> dict[str, Any]:
    get_video_or_404(video_id)
    return {"stopped": watch.stop(video_id)}


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
    try:  # reuse the same limits as links; checked before the file is stored
        options = NewVideo(url="https://youtu.be/upload", shape=shape, max_clips=max_clips, min_s=min_s, max_s=max_s,
                           direction=direction)
    except ValidationError as error:
        raise HTTPException(422, "; ".join(str(e["msg"]).removeprefix("Value error, ") for e in error.errors())) from error
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
    if queue.busy_under(video_id):
        raise HTTPException(409, "Wait for the current job on this video (analysis, export or OpusClip) to finish first.")
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
                                              "look", "shape", "source"}),
                      "start": first.start if first else 0, "end": last.end if last else 0,
                      "exported": (library.clip_dir(video_id, c.index) / "export.mp4").is_file()})
    zip_job = queue.active_for(f"{video_id}/zip")
    opus_job = queue.active_for(f"{video_id}/opusclip")
    return {**_summary(video), "heatmap": video.heatmap, "language": video.language,
            "opus_job": opus_job["id"] if opus_job else None, "opus_done": bool(video.options.get("opusclip_imported")),
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
    background: Background = "blur"
    bg_color: str = Field(default="#111111", pattern=HEX_COLOUR)
    bg_color2: str = Field(default="#3A2A10", pattern=HEX_COLOUR)
    padding: int = Field(default=0, ge=0, le=20)
    radius: int = Field(default=0, ge=0, le=80)
    manual_keys: list[CropKey] = Field(default=[], max_length=2000)
    split_cam: list[UnitFloat] | None = Field(default=None, min_length=4, max_length=4)
    split_bottom: float | None = Field(default=None, ge=0, le=1)
    hook: str = Field(default="", max_length=MAX_TEXT)
    show_hook: bool = True
    show_handle: bool = True
    show_credit: bool = True
    swear_filter: bool = False
    emoji_words: dict[int, str] = Field(default={}, max_length=50)
    show_emoji: bool = True
    music: str = Field(default="", max_length=90)
    music_volume: int = Field(default=15, ge=0, le=100)
    music_peak: int = Field(default=15, ge=0, le=100)
    riser_s: int = Field(default=0, ge=0, le=15)
    show_logo: bool = False
    logo_corner: Literal["tl", "tr", "bl", "br"] = "tr"
    logo_size: int = Field(default=14, ge=6, le=30)
    show_follow: bool = False
    template: Literal["none", "tweet"] = "none"
    tweet_theme: Literal["light", "dark"] = "light"
    title: str = Field(default="", max_length=MAX_TEXT)
    description: str = Field(default="", max_length=1000)
    hashtags: str = Field(default="", max_length=MAX_TEXT)
    status: str = Field(default="new", pattern="^(new|kept|discarded)$")
    rating: int | None = Field(default=None, ge=1, le=10)
    tighten: bool = True
    punch_in: bool = True
    sfx: bool = True
    sfx_safe_only: bool = False
    meme_word: int | None = None
    emphasis_words: list[int] = []
    show_progress: bool = True
    broll: list[dict] = Field(default=[], max_length=10)
    show_broll: bool = True

    @field_validator("emoji_words")
    @classmethod
    def emojis_are_emoji(cls, value: dict[int, str]) -> dict[int, str]:
        if not all(is_emoji(e) for e in value.values()):
            raise ValueError("Only single emoji can be added to a word")
        return value

    @field_validator("music")
    @classmethod
    def music_is_in_the_library(cls, value: str) -> str:
        if value and not assets.MUSIC_NAME.fullmatch(value):
            raise ValueError("Pick a track from the music library.")
        return value

    @field_validator("hashtags")
    @classmethod
    def tidy_hashtags(cls, value: str) -> str:
        return clean_hashtags(value)

    @field_validator("text_fixes")
    @classmethod
    def fixes_are_short(cls, value: dict[int, str]) -> dict[int, str]:
        if any(len(text) > 60 or not text.strip() for text in value.values()):
            raise ValueError("A corrected word must be 1-60 characters")
        return {k: v.strip() for k, v in value.items()}


def export_options(settings: dict[str, Any]) -> dict[str, Any]:
    """Handle, 4K (quality 2) and output frame rate (None = the source's) from Settings."""
    fps = settings.get("export_fps", "source")
    return {"handle": settings.get("handle", ""), "brand": settings.get("brand", ""), "quality": 2 if settings.get("export_quality") == "4k" else 1,
            "fps": None if fps == "source" else float(fps)}


def _plan_for(video: Video, clip: Clip) -> dict[str, Any]:
    settings = load_settings()
    return build_plan(video, clip, handle=settings.get("handle", ""), brand=settings.get("brand", "")).model_dump()


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


@app.get("/api/videos/{video_id}/clips/{index}/tweet.png")
def get_tweet_header(video_id: str, index: int) -> Response:
    """The X post header the export will use, for the preview."""
    from app.template import tweet_header
    video, clip = get_video_or_404(video_id), get_clip_or_404(video_id, index)
    settings = load_settings()
    plan = build_plan(video, clip, handle=settings.get("handle", ""), brand=settings.get("brand", ""))
    if not plan.tweet:
        _not_found("Post header")
    buffer = io.BytesIO()
    tweet_header(plan).save(buffer, "PNG")
    return Response(buffer.getvalue(), media_type="image/png")


POSTER_W = 270


@app.get("/api/videos/{video_id}/clips/{index}/poster.jpg")
def clip_poster(video_id: str, index: int) -> FileResponse:
    """A small still of the clip as it will look (its crop, one second in), cached until the clip changes."""
    video, clip = get_video_or_404(video_id), get_clip_or_404(video_id, index)
    folder = library.clip_dir(video_id, index)
    poster = folder / "poster.jpg"
    if poster.is_file() and poster.stat().st_mtime >= (folder / "clip.json").stat().st_mtime:
        return FileResponse(poster, media_type="image/jpeg")
    try:
        source, offset = library.media_for(video_id, clip)
    except FileNotFoundError:
        _not_found("Poster")
    plan = build_plan(video, clip)
    t_out = min(1.0, plan.duration / 2)
    src_t = plan.segments[0].start_frame / plan.fps + t_out - offset / plan.fps
    sw, sh = plan.source_width, plan.source_height
    if plan.framing == "crop":
        x = min(max(plan.crop_at(t_out) * sw - plan.crop_w / 2, 0), sw - plan.crop_w)
        y = (sh - plan.crop_h) / 2
        crop = f"crop=iw*{plan.crop_w / sw:.5f}:ih*{plan.crop_h / sh:.5f}:iw*{x / sw:.5f}:ih*{y / sh:.5f},"
    else:  # whole frame or split: the middle of the picture at the clip's shape
        crop = f"crop='min(iw,ih*{plan.width / plan.height:.5f})':ih,"
    result = subprocess.run([FFMPEG, "-v", "error", "-y", "-ss", f"{max(src_t, 0):.3f}", "-i", str(source), "-frames:v", "1",
                             "-vf", f"{crop}scale={POSTER_W}:-2", "-q:v", "4", str(poster)], capture_output=True)
    if result.returncode != 0 or not poster.is_file():
        _not_found("Poster")
    return FileResponse(poster, media_type="image/jpeg")


class StatusIn(BaseModel):
    status: Literal["new", "kept", "discarded"]


KEEP_RATING, DROP_RATING = 8, 3  # a keep or drop teaches later picks, unless the clip already has a rating


@app.post("/api/videos/{video_id}/clips/{index}/status")
def set_status(video_id: str, index: int, body: StatusIn) -> dict[str, Any]:
    """Keep or drop a clip from the review feed in one request."""
    get_video_or_404(video_id)
    clip = get_clip_or_404(video_id, index)
    rating = clip.rating if clip.rating is not None or body.status == "new" else (
        KEEP_RATING if body.status == "kept" else DROP_RATING)
    updated = clip.model_copy(update={"status": body.status, "rating": rating})
    library.save_clip(video_id, updated)
    return {"index": index, "status": updated.status, "rating": updated.rating}


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
    if updated.framing == "split":
        updated = look_for_facecam(video, updated)  # once per clip, a few seconds
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
        answer = ai.ask_ai(prompt, EMOJI_SCHEMA, "You are a short-form video editor. Return only the requested JSON.")
    except Exception as error:
        raise HTTPException(503, f"The AI could not suggest emojis right now: {str(error)[:200]}") from error
    picks = {e["word_id"]: e["emoji"].strip() for e in answer.get("emojis", [])
             if e.get("word_id") in ids and is_emoji(e.get("emoji", "").strip())}
    if not picks:
        raise HTTPException(502, "The AI did not return usable emojis. Try again.")
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
    options = export_options(load_settings())
    plan = build_plan(video, clip, handle=options["handle"], quality=options["quality"], brand=options["brand"])
    out = library.clip_dir(video_id, index) / "export.mp4"

    def work(log):
        cleanup.render_export(plan, source, out, log, offset=offset, fps=options["fps"], force=True)
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
        log("Asking the AI for that moment")
        direction = (f"The user is looking for this specific moment: {body.query.strip()}. Return only moments that "
                     "match it, best match first; return none if nothing matches.")
        try:
            clips = find_clips(video, ai.ask_ai, min_s=o.get("min_s", 20), max_s=o.get("max_s", 60), max_clips=3,
                               direction=direction)
        except (ai.AIUnavailable, ai.AIError) as error:
            raise RuntimeError(f"The AI could not answer: {error}") from error
        if not clips:
            raise RuntimeError("The AI found nothing matching that description.")
        return {"added": add_clips(video, clips, log)}

    return {"job": queue.submit("analysis", "find", video_id, work)}


@app.post("/api/videos/{video_id}/opusclip")
def ask_opusclip(video_id: str) -> dict[str, str]:
    """Import OpusClip's picks for this video as extra clips. Runs on its own lane: OpusClip can take a while."""
    video = get_video_or_404(video_id)
    key = load_settings().get("opusclip_key", "")
    if not key:
        raise HTTPException(409, "Add your OpusClip API key in Settings first.")
    if video.stage != "ready":
        raise HTTPException(409, "Wait until this video has finished analysing.")
    if not is_youtube_url(video.url):
        raise HTTPException(409, "OpusClip needs a public link, so it only works for videos added from YouTube.")
    if video.options.get("opusclip_imported"):
        raise HTTPException(409, "OpusClip's picks are already in the list.")
    if active := queue.active_for(f"{video_id}/opusclip"):
        return {"job": active["id"]}

    def work(log):
        current = library.load_video(video_id)
        project = current.options.get("opusclip_project")
        if project:
            log(f"Picking up OpusClip project {project} again (no new credits spent)")
        else:
            o = current.options
            project = opusclip.submit(key, current.url, o.get("min_s", 20), o.get("max_s", 60))
            current = current.model_copy(update={"options": {**current.options, "opusclip_project": project}})
            library.save_video(current)  # before waiting, so a restart never submits (and pays) twice
            log(f"Sent to OpusClip as project {project}")
        clips = opusclip.to_clips(current, opusclip.wait_for_clips(key, project, log))
        if not clips:
            raise RuntimeError("OpusClip finished, but none of its clips lined up with this video's transcript.")
        added = add_clips(current, clips, log)
        latest = library.load_video(video_id)
        library.save_video(latest.model_copy(update={"options": {**latest.options, "opusclip_imported": True}}))
        return {"added": added}

    return {"job": queue.submit("opusclip", "opusclip", f"{video_id}/opusclip", work)}


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
    options = export_options(load_settings())
    work = lambda log: {"video_id": video_id, "zip": cleanup.export_all(video_id, options, log).name}
    return {"job": queue.submit("export", "export-all", subject, work)}


@app.get("/api/videos/{video_id}/clips.zip")
def download_zip(video_id: str) -> FileResponse:
    video = get_video_or_404(video_id)
    path = library.video_dir(video_id) / "clips.zip"
    if not path.is_file():
        _not_found("Zip")
    name = "".join(ch for ch in video.title if ch.isalnum() or ch in " -_").strip()[:60] or video.id
    return FileResponse(path, media_type="application/zip", filename=f"{name} clips.zip")


# ---------- brand assets: music library and logo ----------

@app.get("/api/music")
def music_list() -> list[dict[str, Any]]:
    return assets.list_music()


@app.post("/api/music")
def music_upload(file: UploadFile = File(...)) -> list[dict[str, Any]]:
    try:
        assets.save_music(file.filename or "", file.file)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return assets.list_music()


@app.get("/api/music/{name}")
def music_file(name: str) -> FileResponse:
    try:
        return FileResponse(assets.music_path(name))
    except KeyError:
        _not_found("Track")


@app.delete("/api/music/{name}")
def music_delete(name: str) -> list[dict[str, Any]]:
    try:
        assets.delete_music(name)
    except KeyError:
        _not_found("Track")
    return assets.list_music()


@app.get("/api/logo")
def logo_info() -> dict[str, Any]:
    size = assets.logo_size()
    return {"has_logo": size is not None, "width": size[0] if size else 0, "height": size[1] if size else 0}


@app.post("/api/logo")
def logo_upload(file: UploadFile = File(...)) -> dict[str, Any]:
    try:
        assets.save_logo(file.file)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return logo_info()


@app.delete("/api/logo")
def logo_delete() -> dict[str, Any]:
    assets.delete_logo()
    return logo_info()


@app.get("/api/logo.png")
def logo_file() -> FileResponse:
    if not assets.LOGO_FILE.is_file():
        _not_found("Logo")
    return FileResponse(assets.LOGO_FILE, media_type="image/png")


# ---------- sound effects ----------

class SoundLink(BaseModel):
    url: str = Field(max_length=500)
    role: str


@app.get("/api/sfx")
def sfx_list() -> list[dict[str, Any]]:
    return sfx.list_sounds()


@app.post("/api/sfx/import")
def sfx_import(body: SoundLink) -> list[dict[str, Any]]:
    try:
        sfx.import_myinstants(body.url, body.role)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    except OSError as error:
        raise HTTPException(502, f"Could not download that sound: {error}") from error
    return sfx.list_sounds()


@app.post("/api/sfx")
def sfx_upload(file: UploadFile = File(...), role: str = Form("pop")) -> list[dict[str, Any]]:
    try:
        sfx.upload(file.filename or "", file.file, role)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return sfx.list_sounds()


@app.get("/api/sfx/{name}")
def sfx_file(name: str) -> FileResponse:
    try:
        return FileResponse(sfx.path_of(name))
    except KeyError:
        _not_found("Sound")


@app.delete("/api/sfx/{name}")
def sfx_delete(name: str) -> list[dict[str, Any]]:
    try:
        sfx.delete(name)
    except KeyError:
        _not_found("Sound")
    return sfx.list_sounds()


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
