"""Local web server for the clipper. Bind it to 127.0.0.1 only (see run.sh)."""

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator, model_validator

from app import DATA_DIR, ROOT, cookies_path, jobs
from app.retention import fetch_video_info, pick_peaks, retention_note, score_range

SETTINGS_FILE = DATA_DIR / "settings.json"
LOGO_FILE = DATA_DIR / "logo.png"
WEB_DIR = ROOT / "web"

YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
LOCAL_HOSTS = {"127.0.0.1", "localhost"}
SESSION_ID_RE = re.compile(r"\d{8}_\d{6}")
DEVANAGARI_RE = re.compile(r"[ऀ-ॿ]")
DEVANAGARI_FONT = "/System/Library/Fonts/Kohinoor.ttc"
DEFAULT_BASE_URL = "https://api.openai.com/v1"
HOOK_SECONDS = 4.0
MAX_DIRECTION_CHARS = 1000

SECURITY_HEADERS = {
    # Everything the page needs is served from here; nothing loads from the network.
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "media-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    # Always revalidate, so an edited page or a remade clip is never served stale.
    "Cache-Control": "no-cache",
}

app = FastAPI(title="Clipper", docs_url=None, redoc_url=None)


def is_youtube_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and (parsed.hostname or "").lower() in YOUTUBE_HOSTS


@app.middleware("http")
async def local_requests_only(request: Request, call_next):
    """Refuse anything not addressed to, or not coming from, this machine's own page.

    The Host check stops DNS rebinding; the Origin check stops another website
    from driving this server through the user's browser (for example pointing
    the AI endpoint at itself to capture the API key).
    """
    host = (request.headers.get("host") or "").rsplit(":", 1)[0].lower()
    origin = request.headers.get("origin")
    origin_host = (urlparse(origin).hostname or "").lower() if origin else None
    if host not in LOCAL_HOSTS or (origin is not None and origin_host not in LOCAL_HOSTS):
        return JSONResponse({"detail": "This server only answers its own local page."}, status_code=403)
    response = await call_next(request)
    for header, value in SECURITY_HEADERS.items():
        response.headers.setdefault(header, value)
    return response


# ---------- settings ----------

class SettingsIn(BaseModel):
    base_url: str = DEFAULT_BASE_URL
    model: str = Field(min_length=1, max_length=200)
    api_key: str | None = Field(default=None, max_length=500)

    @field_validator("base_url")
    @classmethod
    def base_url_is_http(cls, value: str) -> str:
        parsed = urlparse(value.strip())
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("The AI endpoint must be an http(s) address")
        return value.strip().rstrip("/")


def load_settings() -> dict[str, str]:
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as error:
        raise HTTPException(500, f"Could not read {SETTINGS_FILE.name}: {error}") from error


def save_settings(settings: dict[str, str]) -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Created private from the start, so the key is never world-readable.
    descriptor = os.open(SETTINGS_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(settings, handle, indent=2)
    os.chmod(SETTINGS_FILE, 0o600)


def public_settings(settings: dict[str, str]) -> dict[str, Any]:
    key = settings.get("api_key", "")
    return {
        "base_url": settings.get("base_url", DEFAULT_BASE_URL),
        "model": settings.get("model", ""),
        "api_key_set": bool(key),
        "api_key_hint": key[-4:] if len(key) >= 8 else "",
        "logo_available": LOGO_FILE.exists(),
    }


@app.get("/api/settings")
def get_settings() -> dict[str, Any]:
    return public_settings(load_settings())


@app.put("/api/settings")
def put_settings(body: SettingsIn) -> dict[str, Any]:
    current = load_settings()
    updated = {
        "base_url": body.base_url,
        "model": body.model.strip(),
        "api_key": (body.api_key or "").strip() or current.get("api_key", ""),
    }
    save_settings(updated)
    return public_settings(updated)


# ---------- sessions ----------

def session_dir(session_id: str) -> Path:
    """Resolve a session folder. The id format is the path-traversal guard."""
    if not SESSION_ID_RE.fullmatch(session_id):
        raise HTTPException(404, "Session not found")
    path = DATA_DIR / "sessions" / session_id
    if not (path / "session_data.json").is_file():
        raise HTTPException(404, "Session not found")
    return path


def load_session(session_id: str) -> dict[str, Any]:
    try:
        return json.loads((session_dir(session_id) / "session_data.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise HTTPException(500, f"Session data is unreadable: {error}") from error


def clip_file(session_id: str, index: int) -> Path:
    return session_dir(session_id) / "clips" / f"clip_{index:03d}" / "master.mp4"


def session_view(session_id: str) -> dict[str, Any]:
    from yt_short_clipper_core.srt_parser import parse_timestamp

    data = load_session(session_id)
    video = data.get("video_info") or {}
    video_id = data.get("video_id", "")
    highlights = []
    for position, h in enumerate(data.get("highlights", [])):
        index = h.get("_highlight_index", position)
        start = parse_timestamp(h["start_time"])
        rendered = clip_file(session_id, index).is_file()
        highlights.append({
            "index": index,
            "title": h.get("title", ""),
            "description": h.get("description", ""),
            "hook_text": h.get("hook_text", ""),
            "start_time": h["start_time"],
            "end_time": h["end_time"],
            "start_seconds": round(start, 1),
            "end_seconds": round(parse_timestamp(h["end_time"]), 1),
            "duration_seconds": h.get("duration_seconds"),
            "virality_score": h.get("virality_score"),
            "retention_score": h.get("retention_score"),
            "watch_url": f"https://youtu.be/{video_id}?t={int(start)}" if video_id else data.get("url", ""),
            "clip_url": f"/api/sessions/{session_id}/clips/{index}" if rendered else None,
        })
    # Most-replayed first; highlights with no replay data keep the AI's own ranking.
    highlights.sort(key=lambda h: (-(h["retention_score"] or 0), -(h["virality_score"] or 0)))
    return {
        "session_id": session_id,
        "url": data.get("url", ""),
        "video": {"id": video_id, "title": video.get("title", ""), "channel": video.get("channel", ""),
                  "duration": data.get("video_duration", 0)},
        "heatmap": data.get("heatmap", []),
        "captions_available": bool(data.get("caption_available")),
        "highlights": highlights,
    }


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str) -> dict[str, Any]:
    return session_view(session_id)


@app.get("/api/sessions/{session_id}/clips/{index}")
def get_clip(session_id: str, index: int, download: bool = False) -> FileResponse:
    path = clip_file(session_id, index)
    if index < 0 or not path.is_file():
        raise HTTPException(404, "Clip not found")
    name = f"clip_{session_id}_{index:03d}.mp4" if download else None
    return FileResponse(path, media_type="video/mp4", filename=name)


# ---------- jobs ----------

class HighlightsIn(BaseModel):
    url: str
    num_clips: int = Field(default=5, ge=1, le=15)
    min_seconds: int = Field(default=20, ge=5, le=600)
    max_seconds: int = Field(default=60, ge=10, le=900)
    direction: str = Field(default="", max_length=MAX_DIRECTION_CHARS)
    output_language: str = Field(default="auto", pattern=r"^[A-Za-z-]{2,10}$")

    @field_validator("url")
    @classmethod
    def url_is_youtube(cls, value: str) -> str:
        if not is_youtube_url(value.strip()):
            raise ValueError("Paste a YouTube link (youtube.com or youtu.be)")
        return value.strip()

    @model_validator(mode="after")
    def lengths_are_ordered(self) -> "HighlightsIn":
        if self.max_seconds <= self.min_seconds:
            raise ValueError("Maximum clip length must be greater than the minimum")
        return self


class RenderIn(BaseModel):
    session_id: str
    indices: list[int] = Field(min_length=1, max_length=15)
    reframe: Literal["face", "bars", "blur"] = "face"
    captions: bool = True
    hook: bool = True
    credit: bool = False
    logo: bool = False


def find_highlights_job(body: HighlightsIn, settings: dict[str, str], log: jobs.LogFn) -> dict[str, str]:
    from yt_short_clipper_core.session import find_highlights_only
    from yt_short_clipper_core.srt_parser import parse_timestamp

    log("Reading video details and replay graph...")
    info = fetch_video_info(body.url, cookies_path())
    heatmap = info["heatmap"]
    peaks = pick_peaks(heatmap)
    if heatmap:
        log(f"Replay graph found: {len(peaks)} most-replayed moments passed to the AI")
    else:
        log("This video has no replay graph; the AI picks from the transcript alone")

    session = find_highlights_only(
        url=body.url,
        num_clips=body.num_clips,
        subtitle_language=(info["language"] or "en").split("-")[0],
        output_dir=DATA_DIR,
        cookies_path=cookies_path(),
        ai={"api_key": settings["api_key"], "base_url": settings.get("base_url", DEFAULT_BASE_URL),
            "model": settings["model"]},
        user_direction=body.direction or None,
        output_language=body.output_language,
        log=log,
        min_seconds=body.min_seconds,
        max_seconds=body.max_seconds,
        retention_note=retention_note(peaks),
    )

    scored = [
        {**h, "_highlight_index": index,
         "retention_score": score_range(heatmap, parse_timestamp(h["start_time"]), parse_timestamp(h["end_time"]))}
        for index, h in enumerate(session["highlights"])
    ]
    enriched = {**session, "highlights": scored, "heatmap": heatmap,
                "video_id": info["id"], "video_duration": info["duration"]}
    path = Path(session["session_dir"]) / "session_data.json"
    path.write_text(json.dumps(enriched, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"session_id": Path(session["session_dir"]).name}


def render_job(body: RenderIn, data: dict[str, Any], log: jobs.LogFn) -> dict[str, str]:
    from yt_short_clipper_core.clip_processor import process_selected_highlights

    folder = session_dir(body.session_id)
    chosen = [h for h in data["highlights"] if h.get("_highlight_index") in body.indices]
    for h in chosen:  # rendering again with new options replaces the old clip
        shutil.rmtree(clip_file(body.session_id, h["_highlight_index"]).parent, ignore_errors=True)

    hook_style: dict[str, Any] = {"duration_seconds": HOOK_SECONDS}
    if any(DEVANAGARI_RE.search(h.get("hook_text", "")) for h in chosen):
        hook_style["font_path"] = DEVANAGARI_FONT
    channel = (data.get("video_info") or {}).get("channel", "")

    process_selected_highlights(
        url=data["url"],
        highlights=chosen,
        session_dir=str(folder),
        options={
            "reframeMode": "face" if body.reframe == "face" else "centered",
            "centeredBackground": "blurred" if body.reframe == "blur" else "black",
            "addCaptions": body.captions,
            "addHook": body.hook,
            "addCreditWatermark": body.credit and bool(channel),
            "addWatermark": body.logo and LOGO_FILE.exists(),
        },
        ai={"hook_style": hook_style,
            "credit_watermark": {"text": f"Credit: {channel}"},
            "watermark": {"image_path": str(LOGO_FILE)}},
        log=log,
    )
    return {"session_id": body.session_id}


def start_job(kind: str, work) -> dict[str, str]:
    try:
        return {"job_id": jobs.start(kind, work)}
    except jobs.Busy as busy:
        raise HTTPException(409, str(busy)) from busy


@app.post("/api/highlights")
def post_highlights(body: HighlightsIn) -> dict[str, str]:
    settings = load_settings()
    if not settings.get("api_key") or not settings.get("model"):
        raise HTTPException(400, "Add your AI endpoint, model and API key in Settings first.")
    return start_job("find", lambda log: find_highlights_job(body, settings, log))


@app.post("/api/render")
def post_render(body: RenderIn) -> dict[str, str]:
    data = load_session(body.session_id)
    known = {h.get("_highlight_index") for h in data.get("highlights", [])}
    if not set(body.indices) <= known:
        raise HTTPException(422, "One of the chosen highlights is not in this session")
    return start_job("render", lambda log: render_job(body, data, log))


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Job not found (the server may have restarted)")
    return job


if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
