"""Housekeeping: resume unfinished work after a restart, free disk after 14 days, export a whole video as a zip."""

import threading
import time
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from app import library, queue
from app.media import make_mezzanine
from app.plan import build_plan
from app.render import render

SOURCE_DAYS = 14          # full source and preview copy are deleted this long after a video was added
MEZZ_MARGIN_S = 30.0      # each clip keeps this much video either side, so it can still be trimmed outward
CHECK_EVERY_S = 6 * 3600
DONE_STAGES = ("ready", "failed")
LogFn = Callable[[str], None]


def resume_unfinished(job_for: Callable[[str], Callable]) -> list[str]:
    """Queue analysis again for every video a restart interrupted. Finished stages skip themselves."""
    resumed = []
    for video in library.list_videos():
        if video.stage not in DONE_STAGES:
            queue.submit("analysis", "analyse", video.id, job_for(video.id))
            resumed.append(video.id)
    return resumed


def _is_old(created_at: str, now: datetime) -> bool:
    try:
        return datetime.fromisoformat(created_at) < now - timedelta(days=SOURCE_DAYS)
    except ValueError:
        return False


def clean_video(video_id: str) -> None:
    """Keep a window of the source around every clip, then delete the full source and the preview copy."""
    video = library.load_video(video_id)
    folder = library.video_dir(video_id)
    source = folder / "source.mp4"
    by_id = {w.id: w for w in video.words}
    last_frame = int(video.duration * video.fps)
    for clip in library.load_clips(video_id):
        if clip.mezz_start_frame is not None:
            continue
        start = max(0, round((by_id[clip.first_word].start - MEZZ_MARGIN_S) * video.fps))
        end = min(last_frame, round((by_id[clip.last_word].end + MEZZ_MARGIN_S) * video.fps))
        make_mezzanine(source, library.clip_dir(video_id, clip.index) / "mezz.mp4", start, end, video.fps)
        library.save_clip(video_id, clip.model_copy(update={"mezz_start_frame": start, "mezz_end_frame": end}))
    source.unlink(missing_ok=True)
    (folder / "proxy.mp4").unlink(missing_ok=True)


def run_cleanup(now: datetime | None = None) -> int:
    now = now or datetime.now()
    cleaned = 0
    for video in library.list_videos():
        folder = library.video_dir(video.id)
        if video.stage == "ready" and (folder / "source.mp4").exists() and _is_old(video.created_at, now) \
                and queue.active_for(video.id) is None:
            clean_video(video.id)
            cleaned += 1
    return cleaned


def start_cleanup_timer() -> None:
    def loop() -> None:
        while True:
            try:
                run_cleanup()
            except Exception:  # a failed cleanup must never stop the server; it retries next round
                import traceback
                traceback.print_exc()
            time.sleep(CHECK_EVERY_S)
    threading.Thread(target=loop, daemon=True, name="cleanup").start()


def export_all(video_id: str, handle: str, log: LogFn) -> Path:
    """Render every clip that is not discarded (reusing exports newer than their edits) and zip them."""
    video = library.load_video(video_id)
    clips = [c for c in library.load_clips(video_id) if c.status != "discarded"]
    if not clips:
        raise RuntimeError("Every clip of this video is discarded, so there is nothing to export.")
    files = []
    for number, clip in enumerate(clips, 1):
        folder = library.clip_dir(video_id, clip.index)
        out = folder / "export.mp4"
        if not out.exists() or out.stat().st_mtime < (folder / "clip.json").stat().st_mtime:
            log(f"Rendering clip {number}/{len(clips)}")
            source, offset = library.media_for(video_id, clip)
            render(build_plan(video, clip, handle=handle), source, out, log=lambda m: None, frame_offset=offset)
        files.append((clip, out))
    zip_path = library.video_dir(video_id) / "clips.zip"
    partial = zip_path.with_suffix(".partial")
    with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_STORED) as z:  # MP4s do not compress further
        for clip, path in files:
            safe = "".join(ch for ch in clip.title if ch.isalnum() or ch in " -_").strip()[:60] or "clip"
            z.write(path, f"{clip.index + 1:02d} {safe}.mp4")
        z.writestr("posts.txt", "\n\n".join(
            f"{clip.index + 1:02d} {clip.title}\n{clip.description}\n{clip.hashtags}".strip() for clip, _ in files) + "\n")
    partial.replace(zip_path)
    log(f"Zipped {len(files)} clips")
    return zip_path
