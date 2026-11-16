"""One background job at a time, with a log the page can poll."""

import threading
import uuid
from typing import Any, Callable

LogFn = Callable[[str], None]

MAX_LOG_LINES = 400
MAX_JOBS_KEPT = 20
# Lines that report progress of the same operation replace each other instead
# of piling up (a download emits hundreds of them).
PROGRESS_PREFIXES = ("Download progress", "[download]", "Encoding portrait", "⏳")


class Busy(Exception):
    """Raised when a job is started while another is still running."""


_lock = threading.Lock()
_jobs: dict[str, dict[str, Any]] = {}
_running: str | None = None


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


def _run(job_id: str, work: Callable[[LogFn], Any]) -> None:
    global _running
    try:
        result = work(lambda message: _append(job_id, message))
        outcome = {"status": "done", "result": result}
    except Exception as error:  # the page shows this; the traceback goes to the terminal
        import traceback

        traceback.print_exc()
        outcome = {"status": "error", "error": str(error)[:1000]}
    with _lock:
        _jobs[job_id].update(outcome)
        _running = None


def start(kind: str, work: Callable[[LogFn], Any]) -> str:
    """Run ``work(log)`` in a thread and return the job id. One job at a time."""
    global _running
    with _lock:
        if _running is not None:
            raise Busy("Another job is still running. Wait for it to finish.")
        job_id = uuid.uuid4().hex[:12]
        _jobs[job_id] = {"id": job_id, "kind": kind, "status": "running",
                         "log": [], "result": None, "error": None}
        _running = job_id
        for stale in list(_jobs)[:-MAX_JOBS_KEPT]:
            del _jobs[stale]
    threading.Thread(target=_run, args=(job_id, work), daemon=True, name=f"job-{kind}").start()
    return job_id


def get(job_id: str) -> dict[str, Any] | None:
    """A snapshot of the job, safe to serialise while the job keeps running."""
    with _lock:
        job = _jobs.get(job_id)
        return {**job, "log": list(job["log"])} if job else None
