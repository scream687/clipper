"""OpusClip as a second clip finder.

Sends the video's link to OpusClip's API, waits for its clips, and maps each clip's time range onto
this video's transcript words, so OpusClip's picks become ordinary Clipper clips (edited, framed and
exported here). OpusClip charges about 1 credit per source minute (10 minimum per API project), so
the project id is stored on the video right after submitting: a retry or a server restart picks the
same project up again instead of paying twice.

Written against https://help.opus.pro/api-reference. The docs do not pin down three things, so they
are read loosely: the project id field name, whether timeRanges are seconds or ms, and when a project
is done (there is no status endpoint; clips simply appear).
"""

import time
from typing import Callable

import requests

from app.model import Clip, Video

API = "https://api.opus.pro/api"
POLL_S = 20
MAX_WAIT_S = 90 * 60
MIN_CLIP_S = 3
REQUEST_TIMEOUT_S = 30

LogFn = Callable[[str], None]


class OpusClipError(RuntimeError):
    pass


def _headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _check(response: requests.Response, doing: str) -> object:
    if response.ok:
        return response.json() if response.content else {}
    detail = response.text.strip()[:300]
    if response.status_code == 401:
        raise OpusClipError("OpusClip did not accept the API key. Check it in Settings.")
    if response.status_code in (402, 403):
        raise OpusClipError(f"OpusClip refused to {doing}: out of credits, over the monthly API cap, or no API "
                            f"access on this plan. ({detail})")
    raise OpusClipError(f"OpusClip could not {doing} ({response.status_code}): {detail}")


def submit(key: str, video_url: str, min_s: int, max_s: int, post=requests.post) -> str:
    body = {"videoUrl": video_url, "curationPref": {"clipDurations": [[min_s, max_s]]}}
    data = _check(post(f"{API}/clip-projects", json=body, headers=_headers(key), timeout=REQUEST_TIMEOUT_S),
                  "start a project")
    inner = data.get("data") if isinstance(data, dict) and isinstance(data.get("data"), dict) else data
    project_id = isinstance(inner, dict) and (inner.get("projectId") or inner.get("id"))
    if not project_id:
        raise OpusClipError(f"OpusClip started a project but its reply had no project id: {str(data)[:300]}")
    return str(project_id)


def fetch_clips(key: str, project_id: str, get=requests.get) -> list[dict]:
    response = get(f"{API}/exportable-clips", params={"q": "findByProjectId", "projectId": project_id},
                   headers=_headers(key), timeout=REQUEST_TIMEOUT_S)
    if response.status_code == 429:
        return []  # rate limited: treat as "not yet", the poll waits anyway
    data = _check(response, "list the project's clips")
    items = data.get("data", data) if isinstance(data, dict) else data
    return [c for c in items if isinstance(c, dict)] if isinstance(items, list) else []


def wait_for_clips(key: str, project_id: str, log: LogFn, get=requests.get, sleep=time.sleep) -> list[dict]:
    """No status endpoint exists, so poll until clips appear."""
    waited = 0
    while waited <= MAX_WAIT_S:
        clips = fetch_clips(key, project_id, get)
        if clips:
            return clips
        log(f"Waiting for OpusClip ({waited // 60} min so far; long videos take a while)")
        sleep(POLL_S)
        waited += POLL_S
    raise OpusClipError(f"OpusClip had no clips after {MAX_WAIT_S // 60} minutes. Try again later; the same "
                        "OpusClip project is reused, so no extra credits are spent.")


def _span_seconds(raw: dict, duration: float) -> tuple[float, float] | None:
    """Overall start and end of a clip in seconds. ponytail: a clip with jump cuts becomes one continuous span."""
    ranges = [r for r in raw.get("timeRanges") or [] if isinstance(r, (list, tuple)) and len(r) >= 2]
    if not ranges:
        return None
    start, end = float(min(r[0] for r in ranges)), float(max(r[1] for r in ranges))
    if end > duration + 5:  # the docs say seconds but show ms-sized numbers; ms values exceed the duration
        start, end = start / 1000, end / 1000
    return (start, end) if end - start >= MIN_CLIP_S else None


def to_clips(video: Video, raw_clips: list[dict]) -> list[Clip]:
    """OpusClip's clips (best first, as returned) as Clipper clips over this video's words."""
    clips = []
    for raw in raw_clips:
        span = _span_seconds(raw, video.duration)
        if not span:
            continue
        inside = [w.id for w in video.words if w.end > span[0] and w.start < span[1]]
        if not inside:
            continue
        hashtags = raw.get("hashtags") or ""
        if isinstance(hashtags, list):
            hashtags = " ".join(hashtags)
        clips.append(Clip(index=0, first_word=inside[0], last_word=inside[-1], source="opusclip",
                          title=str(raw.get("title") or "OpusClip pick")[:120],
                          description=str(raw.get("description") or "")[:2000], hashtags=str(hashtags)[:500]))
    return clips


if __name__ == "__main__":
    from app.model import Word
    words = [Word(id=i, text="w", display="w", start=float(i), end=i + 0.8) for i in range(100)]
    v = Video(id="x", title="t", duration=100, fps=25, width=1, height=1, words=words)
    ms, sec = to_clips(v, [{"timeRanges": [[10000, 20000]], "title": "a", "hashtags": ["#x", "#y"]}]), \
        to_clips(v, [{"timeRanges": [[10, 15], [18, 20]]}, {"timeRanges": [[5, 6]]}])
    assert (ms[0].first_word, ms[0].last_word, ms[0].hashtags) == (10, 19, "#x #y")
    assert len(sec) == 1 and (sec[0].first_word, sec[0].last_word) == (10, 19)
    print("ok")
