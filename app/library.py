"""Where videos and clips live on disk: data/videos/<id>/{video.json, source.mp4, proxy.mp4, clips/<n>/clip.json}."""

import re
import shutil
from datetime import datetime
from pathlib import Path

from app import DATA_DIR
from app.model import Clip, Video, read_json, write_json

VIDEOS = DATA_DIR / "videos"
ID_RE = re.compile(r"[0-9]{8}-[0-9]{6}-[A-Za-z0-9_-]{1,40}")


def new_id(slug: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]", "", slug)[:40] or "video"
    return f"{datetime.now():%Y%m%d-%H%M%S}-{safe}"


def video_dir(video_id: str) -> Path:
    """The id format is the guard against path traversal."""
    if not ID_RE.fullmatch(video_id):
        raise KeyError(video_id)
    return VIDEOS / video_id


def load_video(video_id: str) -> Video:
    path = video_dir(video_id) / "video.json"
    if not path.is_file():
        raise KeyError(video_id)
    return read_json(path, Video)


def save_video(video: Video) -> None:
    write_json(video_dir(video.id) / "video.json", video)


def clip_dir(video_id: str, index: int) -> Path:
    if index < 0:
        raise KeyError(index)
    return video_dir(video_id) / "clips" / f"{index:03d}"


def load_clips(video_id: str) -> list[Clip]:
    root = video_dir(video_id) / "clips"
    return [read_json(p, Clip) for p in sorted(root.glob("*/clip.json"))] if root.is_dir() else []


def load_clip(video_id: str, index: int) -> Clip:
    path = clip_dir(video_id, index) / "clip.json"
    if not path.is_file():
        raise KeyError(index)
    return read_json(path, Clip)


def save_clip(video_id: str, clip: Clip) -> None:
    write_json(clip_dir(video_id, clip.index) / "clip.json", clip)


def list_videos() -> list[Video]:
    if not VIDEOS.is_dir():
        return []
    found = []
    for path in sorted(VIDEOS.glob("*/video.json"), reverse=True):
        try:
            found.append(read_json(path, Video))
        except (OSError, ValueError):
            continue  # a half-written or foreign folder must not break the library page
    return found


def delete_video(video_id: str) -> None:
    shutil.rmtree(video_dir(video_id), ignore_errors=True)
