"""Background work in two lanes, so an export never waits behind a long analysis.

Each lane runs one job at a time, first in, first out. Jobs keep a short log the page polls.
"""

import queue
import threading
import traceback
import uuid
from typing import Any, Callable, Literal

LogFn = Callable[[str], None]
Lane = Literal["analysis", "export", "opusclip"]

MAX_LOG_LINES = 300
MAX_JOBS_KEPT = 200
# Repeated progress lines replace each other instead of piling up.
PROGRESS_PREFIXES = ("Downloading video", "Rendering", "Framed clip", "Waiting for OpusClip")

_lock = threading.Lock()
_jobs: dict[str, dict[str, Any]] = {}
_lanes: dict[str, "queue.Queue[tuple[str, Callable[[LogFn], Any]]]"] = {}


def _append(job_id: str, message: str) -> None:
    line = str(message).strip()
    if not line:
        return
    with _lock:
        log = _jobs[job_id]["log"]
        prefix = next((p for p in PROGRESS_PREFIXES if line.startswith(p)), None)
        if prefix and log and log[-1].startswith(prefix):
            log[-1] = line
        else:
            log.append(line)
        del log[:-MAX_LOG_LINES]


def _worker(lane: str) -> None:
    while True:
        job_id, work = _lanes[lane].get()
        with _lock:
            _jobs[job_id]["status"] = "running"
        try:
            result = work(lambda message: _append(job_id, message))
            update = {"status": "done", "result": result}
        except Exception as error:  # shown on the page; the traceback goes to the terminal
            traceback.print_exc()
            update = {"status": "error", "error": str(error)[:1000]}
        with _lock:
            _jobs[job_id].update(update)


def submit(lane: Lane, kind: str, subject: str, work: Callable[[LogFn], Any]) -> str:
    """Queue `work(log)` on a lane and return the job id. `subject` identifies what it is about (video or clip)."""
    with _lock:
        if lane not in _lanes:
            _lanes[lane] = queue.Queue()
            threading.Thread(target=_worker, args=(lane,), daemon=True, name=f"lane-{lane}").start()
        job_id = uuid.uuid4().hex[:12]
        _jobs[job_id] = {"id": job_id, "lane": lane, "kind": kind, "subject": subject, "status": "queued",
                         "log": [], "result": None, "error": None}
        for stale in list(_jobs)[:-MAX_JOBS_KEPT]:
            if _jobs[stale]["status"] in ("done", "error"):
                del _jobs[stale]
    _lanes[lane].put((job_id, work))
    return job_id


def get(job_id: str) -> dict[str, Any] | None:
    with _lock:
        job = _jobs.get(job_id)
        return {**job, "log": list(job["log"])} if job else None


def busy_under(video_id: str) -> bool:
    """True while any job about this video or one of its parts (`id/3`, `id/zip`, `id/opusclip`) is queued or running."""
    with _lock:
        return any(job["status"] in ("queued", "running") and (job["subject"] == video_id or job["subject"].startswith(f"{video_id}/"))
                   for job in _jobs.values())


def active_for(subject: str) -> dict[str, Any] | None:
    """The newest queued or running job about `subject`, if any."""
    with _lock:
        for job in reversed(list(_jobs.values())):
            if job["subject"] == subject and job["status"] in ("queued", "running"):
                return {**job, "log": list(job["log"])}
    return None
