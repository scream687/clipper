"""Watch a running stream's chat and clip the moments it explodes.

YouTube only serves a live stream's chat from now on, so hype has to be caught as it happens: yt-dlp follows the
chat into a file, this reads it every few seconds, and when a spike has settled (the reaction is over) it captures
the last couple of minutes of the stream around it. Each capture becomes its own video in the library, with the
spike marked, and goes through the normal clipping.
"""

import atexit
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from app import cookies_path, library
from app.chat import CHAT_LAG_S, Msg, parse_line, peaks
from app.model import Video
from app.youtube import download_live_window, fetch_info, is_bot_check

POLL_S = 5
SETTLE_S = 45        # wait this long after a spike before capturing, so the clip has the reaction and what follows
BEFORE_S = 90        # ...and capture this much before the moment, for the setup
KEEP_S = 900         # chat kept in memory: enough for the baseline around a spike
FRESH_S = 60         # only spikes that settled this recently: an old window can drift over the bar as the baseline moves
WARMUP_S = 180       # no spikes until this many seconds of chat are in: the usual level is unknown before
CLIPS_PER_SPIKE = 2
LIVE_HYPE_X = 2.5    # a capture costs a transcription and a library entry: only clear spikes (replays use chat.HYPE_X)

_stops: dict[str, threading.Event] = {}
_procs: set[subprocess.Popen] = set()
LogFn = Callable[[str], None]


@atexit.register
def _stop_followers() -> None:  # yt-dlp would otherwise keep following the chat after the server exits
    for proc in list(_procs):
        proc.terminate()


def _info(url: str, log: LogFn, tries: int = 4) -> dict:
    """yt-dlp's details, retried: YouTube refuses now and then, and one refusal must not end a watch."""
    for attempt in range(tries):
        try:
            return fetch_info(url)
        except Exception as error:
            if is_bot_check(error) or attempt == tries - 1:  # sign-in already tried the browser (youtube.signed_in)
                raise
            log(f"YouTube refused ({str(error)[:80]}); trying again in {15 * (attempt + 1)} s")
            time.sleep(15 * (attempt + 1))
    raise AssertionError("unreachable")


def is_watching(video_id: str) -> bool:
    return video_id in _stops


def stop(video_id: str) -> bool:
    event = _stops.get(video_id)
    if event:
        event.set()
    return event is not None


def _mark(video_id: str, watching: bool) -> None:
    """A marker file (not a video.json field, which a running analysis of the same stream rewrites) that a restart
    reads to pick the watch up again."""
    marker = library.video_dir(video_id) / "watching"
    marker.touch() if watching else marker.unlink(missing_ok=True)


def to_resume() -> list[str]:
    """Streams that were being watched when the server stopped (it never got to remove the marker)."""
    return [p.parent.name for p in library.VIDEOS.glob("*/watching")]


class _Tail:
    """New complete lines of a file that another process is still writing (yt-dlp renames .part when done)."""

    def __init__(self, paths: list[Path]):
        self.paths, self.offset, self.rest = paths, 0, ""

    def lines(self) -> list[str]:
        path = next((p for p in self.paths if p.is_file()), None)
        if path is None:
            return []
        with open(path, encoding="utf-8", errors="replace") as f:
            f.seek(self.offset)
            data = f.read()
            self.offset = f.tell()
        *done, self.rest = (self.rest + data).split("\n")
        return done


def capture_window(now: float, moment: float) -> float:
    """Seconds to capture back from the live edge at `now` so the clip starts BEFORE_S before `moment`. Pure."""
    return min(max(now - moment + BEFORE_S, 120.0), 300.0)


def _capture(parent: Video, spike: dict, folder_of: Callable[[str], Path], log: LogFn) -> Video:
    info = _info(parent.url, log)  # fresh: stream manifests expire
    if not info.get("is_live"):
        raise RuntimeError("the stream ended")
    now = time.time()
    window = capture_window(now, spike["time"])
    child = Video(id=library.new_id(f"hype-{parent.id[-8:]}"), url=parent.url, duration=window, fps=0, width=0, height=0,
                  title=f"{info.get('title') or parent.title} · chat hype {datetime.fromtimestamp(spike['chat_at']):%H:%M} "
                        f"({spike['x']}x)",
                  channel=info.get("channel") or parent.channel, shape=parent.shape, content=parent.content,
                  language=parent.language,
                  # the moment, as a time in this capture: the window ends at the live edge (now)
                  chat_peaks=[{**spike, "time": round(window - (now - spike["time"]), 1),
                               "chat_at": round(window - (now - spike["chat_at"]), 1)}],
                  options={**parent.options, "watch_chat": False, "live": True, "comments_read": True, "chat_read": True, "caption_code": None,
                           "content_checked": bool(parent.content), "max_clips": CLIPS_PER_SPIKE,
                           "watched_from": parent.id},
                  created_at=datetime.now().isoformat(timespec="seconds"))
    library.save_video(child)
    download_live_window(info, folder_of(child.id) / "source.mp4", window, log)
    return child


def watch(parent_id: str, on_capture: Callable[[str], None], log: LogFn) -> dict:
    """Follow the chat of the stream behind `parent_id` until it ends or stop() is called."""
    parent = library.load_video(parent_id)
    if not _info(parent.url, log).get("is_live"):
        _mark(parent_id, False)
        log("This stream is not live any more; its chat replay is read when the video is analysed")
        return {"captured": 0}
    stop_event = _stops.setdefault(parent_id, threading.Event())
    _mark(parent_id, True)  # a server restart resumes it (server lifespan)
    folder = library.video_dir(parent_id) / "work"
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("watch.live_chat.json*"):
        old.unlink()
    command = [sys.executable, "-m", "yt_dlp", "--quiet", "--no-warnings", "--skip-download", "--write-subs",
               "--sub-langs", "live_chat", "-o", str(folder / "watch.%(ext)s"), parent.url]
    if cookies_path():
        command[3:3] = ["--cookies", cookies_path()]
    proc = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    _procs.add(proc)
    tail = _Tail([folder / "watch.live_chat.json.part", folder / "watch.live_chat.json"])
    msgs: list[Msg] = []
    done: list[float] = []  # chat_at of spikes already captured
    captured = 0
    log("Watching the live chat for hype moments (spikes count after 3 minutes, once its usual pace is known)")
    try:
        while not stop_event.wait(POLL_S):
            msgs += [m for line in tail.lines() for m in parse_line(line, live=True)]
            now = time.time()
            msgs = [m for m in msgs if m.t >= now - KEEP_S]
            if not msgs or now - msgs[0].t < WARMUP_S:
                continue
            for spike in peaks(msgs, msgs[0].t, now - SETTLE_S, min_x=LIVE_HYPE_X):
                if spike["chat_at"] < now - SETTLE_S - FRESH_S or any(abs(spike["chat_at"] - d) < 60 for d in done):
                    continue
                done.append(spike["chat_at"])
                log(f"Chat exploded at {datetime.fromtimestamp(spike['chat_at']):%H:%M:%S} ({spike['x']}x: "
                    f"{', '.join(spike['sample'][:3])}); capturing it")
                try:
                    child = _capture(parent, spike, library.video_dir, log)
                except Exception as error:  # one failed capture must not end the watch
                    log(f"Could not capture that moment ({str(error)[:160]})")
                    if "ended" in str(error):
                        break
                    continue
                captured += 1
                on_capture(child.id)
                log(f"Hype moments captured: {captured}")
            if proc.poll() is not None:
                log("The chat stopped (the stream ended or YouTube closed it)")
                break
    finally:
        proc.terminate()
        _procs.discard(proc)
        _stops.pop(parent_id, None)
        _mark(parent_id, False)
    log(f"Stopped watching; {captured} hype moment{'s' if captured != 1 else ''} captured")
    return {"captured": captured}
