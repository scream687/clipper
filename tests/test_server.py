import pytest
from fastapi.testclient import TestClient

from app import library, server
from app.model import Clip, Video, Word


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(library, "VIDEOS", tmp_path / "videos")
    return TestClient(server.app, base_url="http://127.0.0.1:8765")


@pytest.fixture
def stored(client):
    words = [Word(id=i, text=f"w{i}", display=f"w{i}", start=i * 0.5, end=i * 0.5 + 0.4) for i in range(40)]
    video = Video(id="20261117-120000-abc", title="T", channel="C", duration=30, fps=25, width=1920, height=1080,
                  words=words, stage="ready")
    library.save_video(video)
    library.save_clip(video.id, Clip(index=0, first_word=2, last_word=20, title="A clip"))
    return video


@pytest.mark.parametrize("url", ["https://www.youtube.com/watch?v=arj7oStGLkU", "https://youtu.be/x", "https://m.youtube.com/watch?v=1"])
def test_youtube_links_are_accepted(url):
    assert server.is_youtube_url(url)


@pytest.mark.parametrize("url", ["https://youtube.com.evil.example/x", "file:///etc/passwd", "ftp://youtube.com/x", "nope", ""])
def test_other_links_are_rejected(url):
    assert not server.is_youtube_url(url)


def test_adding_a_non_youtube_link_is_refused(client):
    assert client.post("/api/videos", json={"url": "https://vimeo.com/1"}).status_code == 422


def test_adding_with_shortest_above_longest_is_refused(client):
    assert client.post("/api/videos", json={"url": "https://youtu.be/x", "min_s": 60, "max_s": 30}).status_code == 422


@pytest.mark.parametrize("vid", ["..", "..%2F..%2Fetc", "abc", "20261117-120000-a/b"])
def test_bad_video_ids_are_not_found(client, vid):
    assert client.get(f"/api/videos/{vid}").status_code == 404
    assert client.get(f"/api/videos/{vid}/proxy.mp4").status_code == 404


def test_library_lists_stored_videos(client, stored):
    listed = client.get("/api/videos").json()
    assert [v["id"] for v in listed] == [stored.id] and listed[0]["clip_count"] == 1


def test_clip_comes_with_its_plan(client, stored):
    body = client.get(f"/api/videos/{stored.id}/clips/0").json()
    assert body["clip"]["title"] == "A clip" and body["plan"]["width"] == 1080 and body["plan"]["segments"]


def test_editing_a_clip_saves_and_returns_the_new_plan(client, stored):
    edit = {"first_word": 2, "last_word": 20, "cut_word_ids": [5, 6], "text_fixes": {"8": "fixed"}, "look": "karaoke", "shape": "1:1"}
    body = client.put(f"/api/videos/{stored.id}/clips/0", json=edit).json()
    assert body["plan"]["height"] == 1080 and len(body["plan"]["segments"]) == 2
    assert library.load_clip(stored.id, 0).text_fixes == {8: "fixed"}


@pytest.mark.parametrize("edit", [
    {"first_word": 20, "last_word": 2},
    {"first_word": 2, "last_word": 999},
    {"first_word": 2, "last_word": 20, "cut_word_ids": [30]},
    {"first_word": 2, "last_word": 4, "cut_word_ids": [2, 3, 4]},
    {"first_word": 2, "last_word": 20, "text_fixes": {"5": "   "}},
    {"first_word": 2, "last_word": 20, "look": "neon"},
])
def test_invalid_edits_are_refused(client, stored, edit):
    assert client.put(f"/api/videos/{stored.id}/clips/0", json=edit).status_code == 422


def test_handle_setting_reaches_the_plan(client, stored):
    client.put("/api/settings", json={"handle": "@mine"})
    assert client.get(f"/api/videos/{stored.id}/clips/0").json()["plan"]["handle"] == "@mine"


def test_export_without_source_explains_why(client, stored):
    response = client.post(f"/api/videos/{stored.id}/clips/0/export")
    assert response.status_code == 409 and "no longer on disk" in response.json()["detail"]


def test_export_all_with_every_clip_discarded_reports_why(client, stored):
    from app import queue
    library.save_clip(stored.id, library.load_clip(stored.id, 0).model_copy(update={"status": "discarded"}))
    job = client.post(f"/api/videos/{stored.id}/export-all").json()["job"]
    import time
    for _ in range(50):
        if queue.get(job)["status"] in ("done", "error"): break
        time.sleep(0.1)
    assert queue.get(job)["status"] == "error" and "discarded" in queue.get(job)["error"]


def test_only_the_two_caption_fonts_are_served(client):
    assert client.get("/fonts/arial-black.ttf").status_code == 200
    assert client.get("/fonts/..%2F..%2Fetc%2Fpasswd").status_code == 404


def test_requests_for_another_host_or_from_another_site_are_refused(client):
    assert client.get("/api/settings", headers={"host": "evil.example"}).status_code == 403
    assert client.put("/api/settings", json={"handle": "x"}, headers={"origin": "https://evil.example"}).status_code == 403


def test_upload_is_stored_and_queued(client, monkeypatch):
    from app import queue
    queued = []
    monkeypatch.setattr(queue, "submit", lambda lane, kind, subject, work: queued.append(subject) or "job1")
    body = client.post("/api/uploads", files={"file": ("my talk.mov", b"0" * 1000, "video/quicktime")},
                       data={"language": "hi", "shape": "1:1"}).json()
    video = library.load_video(body["id"])
    assert queued == [video.id] and video.shape == "1:1" and video.language == "hi" and video.url == ""
    assert (library.video_dir(video.id) / "upload.mov").stat().st_size == 1000


@pytest.mark.parametrize("name,lang", [("notes.txt", "hi"), ("clip.mp4", "fr")])
def test_bad_uploads_are_refused(client, name, lang):
    response = client.post("/api/uploads", files={"file": (name, b"x", "application/octet-stream")}, data={"language": lang})
    assert response.status_code == 422
