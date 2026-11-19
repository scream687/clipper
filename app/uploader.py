"""Daily uploads to your YouTube channel: the AI's best new clips go out as Shorts at fixed times (IST).

Uses the YouTube Data API with your own Google Cloud project (client_secret.json in the Clipper folder, a "Desktop
app" OAuth client) and your sign-in, kept in data/youtube_token.json. Each slot's clip is rendered and uploaded up to
LEAD_H hours ahead and scheduled to go public at the slot time. Until Google audits the API project, YouTube keeps
API uploads private: publish them from YouTube Studio (one tap) until then.
"""

import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from app import DATA_DIR, ROOT, library
from app.model import Clip, Video

CLIENT_FILE = ROOT / "client_secret.json"
TOKEN_FILE = DATA_DIR / "youtube_token.json"
LOG_FILE = DATA_DIR / "uploads.json"   # every upload: slot, video, clip, youtube id, title
SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly"]
IST = timezone(timedelta(hours=5, minutes=30))
SLOTS = ((13, 0), (19, 0))   # Indian Shorts traffic peaks
LEAD_H = 20                  # a slot's clip is uploaded up to this long before it goes public
MIN_SCORE = 7                # never post a clip the AI rated below this
CHECK_EVERY_S = 30 * 60
GAMING, ENTERTAINMENT = "20", "24"
LogFn = Callable[[str], None]
_lock = threading.Lock()


def configured() -> bool:
    return CLIENT_FILE.is_file()


def _save(creds) -> None:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as out:
        out.write(creds.to_json())


def credentials():
    """Your saved sign-in, refreshed when it expires; None when not connected."""
    if not TOKEN_FILE.is_file():
        return None
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _save(creds)
    return creds if creds.valid else None


def connect(log: LogFn) -> str:
    """Open Google's sign-in in the browser, keep the token, and return the channel's name."""
    if not configured():
        raise RuntimeError("client_secret.json is missing from the Clipper folder (see README: YouTube uploads).")
    from google_auth_oauthlib.flow import InstalledAppFlow
    log("Opening Google sign-in in your browser: pick the channel's account and allow uploads")
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_FILE), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True, prompt="consent", access_type="offline",
                                  authorization_prompt_message="", success_message="Clipper is connected. You can close this tab.")
    _save(creds)
    name = channel_name() or "your channel"
    log(f"Connected to {name}")
    return name


def disconnect() -> None:
    TOKEN_FILE.unlink(missing_ok=True)


def _api():
    from googleapiclient.discovery import build
    creds = credentials()
    if creds is None:
        raise RuntimeError("YouTube is not connected: Settings > YouTube channel > Connect.")
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def channel_name() -> str | None:
    items = _api().channels().list(part="snippet", mine=True).execute().get("items", [])
    return items[0]["snippet"]["title"] if items else None


def history() -> list[dict[str, Any]]:
    try:
        return json.loads(LOG_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _record(entry: dict[str, Any]) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.write_text(json.dumps(history() + [entry], indent=1, ensure_ascii=False), encoding="utf-8")


def due_slots(now: datetime, filled: set[str], lead_h: float = LEAD_H) -> list[datetime]:
    """Slot times (IST) within lead_h hours from now that have no upload yet. Pure."""
    now = now.astimezone(IST)
    out = []
    for day in (0, 1):
        for hour, minute in SLOTS:
            slot = (now + timedelta(days=day)).replace(hour=hour, minute=minute, second=0, microsecond=0)
            if now + timedelta(minutes=15) < slot <= now + timedelta(hours=lead_h) and slot.isoformat() not in filled:
                out.append(slot)
    return out


def pick(candidates: list[tuple[Video, Clip]], done: set[str], n: int) -> list[tuple[Video, Clip]]:
    """The AI's best clips not posted yet: score, then how much viewers replayed or mentioned it; at most one per
    source video, never a discarded clip or one scored under MIN_SCORE. Pure."""
    fresh = [(v, c) for v, c in candidates if c.status != "discarded" and c.score >= MIN_SCORE and f"{v.id}/{c.index}" not in done]
    fresh.sort(key=lambda vc: vc[0].created_at, reverse=True)  # ties go to the newest video (stable sort below)
    fresh.sort(key=lambda vc: (-vc[1].score, -(vc[1].retention or 0), -(vc[1].mentions or 0)))
    out, used = [], set()
    for v, c in fresh:
        if v.id in used:
            continue
        out.append((v, c))
        used.add(v.id)
        if len(out) == n:
            break
    return out


def metadata(video: Video, clip: Clip) -> dict[str, Any]:
    """Title (with #Shorts), description with hashtags and credit, tags and category."""
    tags = [t.lstrip("#") for t in clip.hashtags.split() if t.startswith("#")]
    title = (clip.title or clip.hook or video.title).strip()
    title = (title[:100 - 8].rstrip() + " #Shorts") if "#shorts" not in title.lower() else title[:100]
    lines = [clip.description.strip(), " ".join(f"#{t}" for t in tags + ["Shorts"])]
    if video.channel:
        lines.append(f"Credit: {video.channel}")
    return {"title": title, "description": "\n\n".join(x for x in lines if x)[:4900], "tags": tags[:15],
            "categoryId": GAMING if video.content in ("gameplay", "stream") else ENTERTAINMENT}


def upload(path: Path, meta: dict[str, Any], publish_at: datetime, log: LogFn) -> str:
    """Upload the MP4, scheduled to go public at publish_at; returns the YouTube video id."""
    from googleapiclient.http import MediaFileUpload
    body = {"snippet": meta, "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False,
                                        "publishAt": publish_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")}}
    request = _api().videos().insert(part="snippet,status", body=body,
                                     media_body=MediaFileUpload(str(path), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True))
    from googleapiclient.errors import HttpError
    response = None
    try:
        while response is None:
            status, response = request.next_chunk()
            if status:
                log(f"Uploading {int(status.progress() * 100)}%")
    except HttpError as error:
        if "quotaExceeded" in str(error) or "uploadLimitExceeded" in str(error):
            raise RuntimeError("YouTube's daily upload limit for this API project is used up; the slot is retried "
                               "tomorrow.") from error
        raise RuntimeError(f"YouTube refused the upload: {str(error)[:300]}") from error
    return response["id"]


def _candidates() -> list[tuple[Video, Clip]]:
    out = []
    for video in library.list_videos():
        if video.stage != "ready":
            continue
        for clip in library.load_clips(video.id):
            try:
                library.media_for(video.id, clip)  # its footage is still on disk
            except FileNotFoundError:
                continue
            out.append((video, clip))
    return out


def tick(options: dict[str, Any], log: LogFn, now: datetime | None = None) -> int:
    """Fill every slot coming up within LEAD_H hours. `options` are the export options (handle, quality, fps).
    Returns how many clips were scheduled."""
    from app.cleanup import render_export
    from app.plan import build_plan
    with _lock:
        past = history()
        slots = due_slots(now or datetime.now(IST), {e["slot"] for e in past})
        if not slots:
            return 0
        chosen = pick(_candidates(), {f"{e['video']}/{e['clip']}" for e in past}, len(slots))
        if not chosen:
            log(f"No new clip scored {MIN_SCORE}+ to post; add videos to fill the next slot")
            return 0
        for slot, (video, clip) in zip(slots, chosen):
            log(f"{slot:%d %b %H:%M} IST: {clip.title}")
            plan = build_plan(video, clip, handle=options.get("handle", ""), quality=options.get("quality", 1),
                              brand=options.get("brand", ""))
            source, offset = library.media_for(video.id, clip)
            out = library.clip_dir(video.id, clip.index) / "export.mp4"
            render_export(plan, source, out, log, offset, options.get("fps"))
            meta = metadata(video, clip)
            youtube_id = upload(out, meta, slot, log)
            _record({"slot": slot.isoformat(), "video": video.id, "clip": clip.index, "youtube_id": youtube_id,
                     "title": meta["title"], "at": datetime.now(IST).isoformat(timespec="seconds")})
            log(f"Scheduled https://youtu.be/{youtube_id} for {slot:%d %b %H:%M} IST")
        return len(chosen[:len(slots)])


def start_timer(enabled: Callable[[], bool], options: Callable[[], dict[str, Any]], submit: Callable[[Callable], Any]) -> None:
    """Every CHECK_EVERY_S, queue a tick when auto-upload is on and YouTube is connected."""
    def loop() -> None:
        while True:
            try:
                if enabled() and TOKEN_FILE.is_file():
                    submit(lambda log: {"scheduled": tick(options(), log)})
            except Exception:  # a failed check must never stop the server; it retries next round
                import traceback
                traceback.print_exc()
            time.sleep(CHECK_EVERY_S)
    threading.Thread(target=loop, daemon=True, name="uploader").start()
