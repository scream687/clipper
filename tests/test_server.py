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
    client.put("/api/settings", json={"handle": " @  mine "})
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


def test_find_needs_a_finished_video(client, stored):
    library.save_video(library.load_video(stored.id).model_copy(update={"stage": "Downloading video"}))
    assert client.post(f"/api/videos/{stored.id}/find", json={"query": "insulin"}).status_code == 409


def test_making_a_clip_from_words_appends_it(client, stored, monkeypatch):
    from app import pipeline
    (library.video_dir(stored.id) / "proxy.mp4").write_bytes(b"x")
    monkeypatch.setattr(pipeline, "frame_clip", lambda video, clip: clip)
    body = client.post(f"/api/videos/{stored.id}/clips", json={"first_word": 22, "last_word": 30}).json()
    assert body["index"] == 1 and library.load_clip(stored.id, 1).first_word == 22


def test_making_a_clip_checks_its_range_and_the_video(client, stored):
    assert client.post(f"/api/videos/{stored.id}/clips", json={"first_word": 30, "last_word": 22}).status_code == 422
    assert client.post(f"/api/videos/{stored.id}/clips", json={"first_word": 22, "last_word": 30}).status_code == 409


def test_music_and_logo_round_trip_and_clips_can_use_them(client, stored, tmp_path, monkeypatch):
    import io, subprocess
    from PIL import Image
    from app import FFMPEG, assets
    monkeypatch.setattr(assets, "MUSIC_DIR", tmp_path / "music")
    monkeypatch.setattr(assets, "LOGO_FILE", tmp_path / "logo.png")
    song = tmp_path / "s.mp3"
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", "sine=duration=1", str(song)], check=True)
    listed = client.post("/api/music", files={"file": ("Lo Fi.mp3", song.read_bytes())}).json()
    assert [m["name"] for m in listed] == ["Lo-Fi.mp3"]
    assert client.get("/api/music/Lo-Fi.mp3").status_code == 200
    assert client.get("/api/music/..%2Fsettings.json").status_code == 404
    png = io.BytesIO(); Image.new("RGB", (100, 50)).save(png, "PNG")
    assert client.post("/api/logo", files={"file": ("l.png", png.getvalue())}).json()["has_logo"]
    clip = client.get(f"/api/videos/{stored.id}/clips/0").json()["clip"]
    body = client.put(f"/api/videos/{stored.id}/clips/0", json={**clip, "music": "Lo-Fi.mp3", "show_logo": True, "show_follow": True}).json()
    assert body["plan"]["music"]["file"] == "Lo-Fi.mp3" and body["plan"]["logo"] and body["plan"]["follow"]
    assert client.put(f"/api/videos/{stored.id}/clips/0", json={**clip, "music": "../x.mp3"}).status_code == 422


def test_upload_with_bad_lengths_is_refused_before_storing(client):
    response = client.post("/api/uploads", files={"file": ("talk.mp4", b"0" * 10, "video/mp4")},
                           data={"language": "hi", "min_s": "60", "max_s": "30"})
    assert response.status_code == 422 and "longest" in response.json()["detail"].lower()
    assert library.list_videos() == []


def test_delete_waits_for_an_export_of_one_of_its_clips(client, stored, monkeypatch):
    from app import queue
    monkeypatch.setattr(queue, "_jobs", {"j": {"subject": f"{stored.id}/0", "status": "running"}})
    assert client.delete(f"/api/videos/{stored.id}").status_code == 409
    monkeypatch.setattr(queue, "_jobs", {"j": {"subject": f"{stored.id}/0", "status": "done"}})
    assert client.delete(f"/api/videos/{stored.id}").status_code == 200


def test_clips_added_at_the_same_time_get_different_indices(client, stored, monkeypatch):
    import threading
    from app import pipeline
    monkeypatch.setattr(pipeline, "frame_clip", lambda video, clip: None)
    out = []
    jobs = [threading.Thread(target=lambda: out.extend(pipeline.add_clips(stored, [Clip(index=0, first_word=1, last_word=5)], lambda m: None)))
            for _ in range(8)]
    [t.start() for t in jobs]; [t.join() for t in jobs]
    assert sorted(out) == list(range(1, 9))


def test_an_edit_keeps_the_ai_picks_it_sends_back(client, stored):
    broll = [{"first_word": 8, "last_word": 10, "kind": "card", "text": "90%", "sub": "", "query": ""}]
    edit = {"first_word": 2, "last_word": 20, "emphasis_words": [5], "meme_word": 7, "broll": broll}
    body = client.put(f"/api/videos/{stored.id}/clips/0", json=edit).json()
    assert body["clip"]["emphasis_words"] == [5] and body["clip"]["meme_word"] == 7 and body["clip"]["broll"] == broll


def test_keep_and_drop_set_the_status_and_teach_a_rating(client, stored):
    assert client.post(f"/api/videos/{stored.id}/clips/0/status", json={"status": "kept"}).json() == {"index": 0, "status": "kept", "rating": 8}
    assert library.load_clip(stored.id, 0).status == "kept"
    assert client.post(f"/api/videos/{stored.id}/clips/0/status", json={"status": "discarded"}).json()["rating"] == 8  # rated stays
    assert client.post(f"/api/videos/{stored.id}/clips/0/status", json={"status": "maybe"}).status_code == 422


def test_live_links_skip_the_queue_and_watch_only_after_they_are_confirmed(monkeypatch):
    from app import queue, server
    from app.model import Video
    lanes, watched = [], []
    monkeypatch.setattr(queue, "submit", lambda lane, kind, subject, work: lanes.append(lane) or "job")
    live = Video(id="20260101-000000-l", url="https://www.youtube.com/live/abc", title="", duration=0, fps=0, width=0, height=0)
    vod = live.model_copy(update={"url": "https://youtu.be/abc"})
    capture = live.model_copy(update={"options": {"watched_from": "x"}})
    for v in (live, vod, capture):
        server._submit_analysis(v)
    assert lanes == ["live", "analysis", "analysis"]  # a boom capture transcribes: it queues with the rest

    done = live.model_copy(update={"options": {"live": True, "watch_chat": True}})
    monkeypatch.setattr(server, "analyse", lambda vid, log: done)
    monkeypatch.setattr(server, "_start_watch", lambda vid: watched.append(vid))
    monkeypatch.setattr(server, "_watch_status", lambda vid: None)
    server._analyse_job(done.id)(lambda m: None)
    assert watched == [done.id]


def test_a_listed_streamer_going_live_is_added_once_and_watched(client, monkeypatch):
    client.put("/api/settings", json={"live_channels": ["@CarryMinati", "@offline"]})
    live = {"@CarryMinati": "https://www.youtube.com/live/abc123"}
    monkeypatch.setattr(server.channels, "live_now", lambda h: live.get(h))
    submitted = []
    monkeypatch.setattr(server, "_submit_analysis", lambda v: submitted.append(v) or "job1")
    [vid] = server.check_live_channels(log=lambda _: None)
    video = library.load_video(vid)
    assert video.url == live["@CarryMinati"] and video.options["watch_chat"] and video.options["auto_from"] == "@CarryMinati"
    assert server.check_live_channels(log=lambda _: None) == [] and len(submitted) == 1  # same stream: not added again


def test_one_channel_failing_does_not_skip_the_others(client, monkeypatch):
    client.put("/api/settings", json={"live_channels": ["@broken", "@live"]})
    def live_now(handle):
        if handle == "@broken":
            raise RuntimeError("sign in")
        return "https://www.youtube.com/live/zzz999"
    monkeypatch.setattr(server.channels, "live_now", live_now)
    monkeypatch.setattr(server, "_submit_analysis", lambda v: "job")
    logs = []
    assert len(server.check_live_channels(log=logs.append)) == 1 and "sign in" in logs[0]
