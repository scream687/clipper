import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import library, opusclip, queue, server
from app.model import Clip, Video, Word


def reply(status=200, body=None):
    import json
    text = json.dumps(body) if body is not None else ""
    return SimpleNamespace(status_code=status, ok=200 <= status < 300, content=text.encode(), text=text,
                           json=lambda: body)


def test_project_id_is_read_from_either_field():
    assert opusclip.submit("k", "u", 20, 60, post=lambda *a, **kw: reply(200, {"id": "P1"})) == "P1"
    assert opusclip.submit("k", "u", 20, 60, post=lambda *a, **kw: reply(200, {"data": {"projectId": "P2"}})) == "P2"
    with pytest.raises(opusclip.OpusClipError, match="credits"):
        opusclip.submit("k", "u", 20, 60, post=lambda *a, **kw: reply(403, {"message": "no credits"}))


def test_waits_until_clips_appear_and_treats_rate_limit_as_not_yet():
    answers = iter([reply(200, []), reply(429, None), reply(200, [{"id": "c", "timeRanges": [[1, 9]]}])])
    clips = opusclip.wait_for_clips("k", "P", lambda m: None, get=lambda *a, **kw: next(answers), sleep=lambda s: None)
    assert clips[0]["id"] == "c"


def test_time_ranges_in_ms_or_seconds_map_onto_words():
    words = [Word(id=i, text="w", display="w", start=float(i), end=i + 0.8) for i in range(100)]
    video = Video(id="v", title="t", duration=100, fps=25, width=1, height=1, words=words)
    [ms] = opusclip.to_clips(video, [{"timeRanges": [[10000, 20000]], "title": "A", "hashtags": ["#a", "#b"]}])
    [sec] = opusclip.to_clips(video, [{"timeRanges": [[10, 15], [17, 20]]}, {"timeRanges": [[50, 51]]}])
    assert (ms.first_word, ms.last_word, ms.hashtags, ms.source) == (10, 19, "#a #b", "opusclip")
    assert (sec.first_word, sec.last_word) == (10, 19)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(library, "VIDEOS", tmp_path / "videos")
    words = [Word(id=i, text="w", display="w", start=float(i), end=i + 0.8) for i in range(100)]
    library.save_video(Video(id="20261117-120000-abc", title="T", url="https://youtu.be/abc", duration=100, fps=25,
                             width=1920, height=1080, words=words, stage="ready"))
    library.save_clip("20261117-120000-abc", Clip(index=0, first_word=0, last_word=20))
    return TestClient(server.app, base_url="http://127.0.0.1:8765")


def run_job(client, video_id):
    job = client.post(f"/api/videos/{video_id}/opusclip").json()["job"]
    for _ in range(100):
        state = queue.get(job)
        if state["status"] in ("done", "error"):
            return state
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_needs_a_key_and_never_returns_it(client):
    vid = "20261117-120000-abc"
    assert client.post(f"/api/videos/{vid}/opusclip").status_code == 409
    saved = client.put("/api/settings", json={"handle": "", "opusclip_key": "op-secret"}).json()
    assert saved["has_opusclip_key"] is True and "op-secret" not in str(saved)
    assert client.put("/api/settings", json={"handle": "x"}).json()["has_opusclip_key"] is True  # blank keeps it


def test_a_retry_reuses_the_paid_project_and_imports_once(client, monkeypatch):
    vid = "20261117-120000-abc"
    client.put("/api/settings", json={"handle": "", "opusclip_key": "op-key"})
    submits = []
    monkeypatch.setattr(opusclip, "submit", lambda key, url, a, b: submits.append(url) or "P9")
    monkeypatch.setattr(opusclip, "wait_for_clips", lambda key, project, log: (_ for _ in ()).throw(opusclip.OpusClipError("slow")))
    assert run_job(client, vid)["status"] == "error"
    assert library.load_video(vid).options["opusclip_project"] == "P9"

    monkeypatch.setattr(opusclip, "wait_for_clips", lambda key, project, log: [{"timeRanges": [[30, 45]], "title": "Opus"}])
    monkeypatch.setattr(server, "add_clips", lambda video, clips, log: [library.save_clip(video.id, clips[0].model_copy(update={"index": 1})) or 1])
    done = run_job(client, vid)
    assert done["status"] == "done" and done["result"] == {"added": [1]} and submits == ["https://youtu.be/abc"]
    assert library.load_clip(vid, 1).source == "opusclip"
    assert client.get(f"/api/videos/{vid}").json()["opus_done"] is True
    assert client.post(f"/api/videos/{vid}/opusclip").status_code == 409
