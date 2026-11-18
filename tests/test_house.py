import json

import pytest
from fastapi.testclient import TestClient

from app import house, library, pipeline, server
from app.model import Clip, Video, Word


@pytest.fixture
def setup(tmp_path, monkeypatch):
    settings = tmp_path / "settings.json"
    monkeypatch.setattr(server, "SETTINGS_FILE", settings)
    monkeypatch.setattr(house, "SETTINGS_FILE", settings)
    monkeypatch.setattr(library, "VIDEOS", tmp_path / "videos")
    monkeypatch.setattr(pipeline, "frame_clip", lambda video, clip: clip)
    words = [Word(id=i, text=f"w{i}", display=f"w{i}", start=i * 0.5, end=i * 0.5 + 0.4) for i in range(40)]
    video = Video(id="20261117-120000-abc", title="T", duration=30, fps=25, width=1920, height=1080, words=words, stage="ready")
    library.save_video(video)
    library.save_clip(video.id, Clip(index=0, first_word=2, last_word=20, look="pop", show_follow=True, music="beat.mp3",
                                     framing="split"))
    library.save_clip(video.id, Clip(index=1, first_word=22, last_word=30))
    return TestClient(server.app, base_url="http://127.0.0.1:8765"), video, settings


def test_a_saved_default_look_is_worn_by_new_clips_but_not_their_framing(setup):
    client, video, settings = setup
    assert client.put("/api/house-look", json={"video_id": video.id, "index": 0}).status_code == 200
    [index] = pipeline.add_clips(video, [Clip(index=0, first_word=5, last_word=9)], lambda m: None)
    new = library.load_clip(video.id, index)
    assert (new.look, new.show_follow, new.music, new.framing) == ("pop", True, "beat.mp3", "crop")


def test_apply_to_all_copies_the_look_to_every_other_clip(setup):
    client, video, _ = setup
    assert client.post(f"/api/videos/{video.id}/apply-look", json={"video_id": video.id, "index": 0}).json() == {"updated": 1}
    other = library.load_clip(video.id, 1)
    assert other.look == "pop" and other.first_word == 22 and other.framing == "crop"


def test_a_broken_saved_look_never_breaks_a_clip(setup):
    _, _, settings = setup
    settings.write_text(json.dumps({"house_look": {"look": "nope", "padding": 999}}))
    clip = Clip(index=0, first_word=1, last_word=2)
    assert house.with_look(clip, house.saved_look()) == clip


def test_classify_returns_only_known_kinds(setup):
    _, video, _ = setup
    assert house.classify(video, lambda p, s, sy: {"content": "gameplay"}) == "gameplay"
    assert house.classify(video, lambda p, s, sy: {"content": "cooking"}) == ""


def test_gameplay_with_no_facecam_shows_the_whole_frame_and_never_follows_game_faces(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "VIDEOS", tmp_path / "videos")
    words = [Word(id=i, text="w", display="w", start=i, end=i + 0.5) for i in range(10)]
    video = Video(id="20261117-120000-gta", title="GTA", duration=10, fps=25, width=1920, height=1080, words=words,
                  content="gameplay")
    library.save_video(video)
    monkeypatch.setattr(pipeline, "auto_framing", lambda *a: pytest.fail("faces must not be followed in gameplay"))
    monkeypatch.setattr(pipeline, "look_for_facecam", lambda v, c: c.model_copy(update={"cam_box": []}))
    framed = pipeline.frame_clip(video, Clip(index=0, first_word=1, last_word=5))
    assert framed.framing == "fill" and framed.framed
    monkeypatch.setattr(pipeline, "look_for_facecam", lambda v, c: c.model_copy(update={"cam_box": [0, 0, 0.2, 0.2]}))
    assert pipeline.frame_clip(video, Clip(index=1, first_word=1, last_word=5)).framing == "split"
