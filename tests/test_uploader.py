from datetime import datetime

from app import uploader
from app.model import Clip, Video
from app.uploader import IST


def _video(vid, content="stream", created="2026-11-18T10:00:00"):
    return Video(id=vid, title="Stream", channel="CarryisLive", duration=600, fps=30, width=1920, height=1080,
                 content=content, created_at=created, stage="ready")


def test_the_next_slots_inside_the_lead_time_are_due_once():
    now = datetime(2026, 11, 19, 10, 0, tzinfo=IST)
    due = uploader.due_slots(now, set())
    assert [d.strftime("%d %H:%M") for d in due] == ["19 13:00", "19 19:00"]
    assert [d.strftime("%d %H:%M") for d in uploader.due_slots(now, {due[0].isoformat()})] == ["19 19:00"]
    late = datetime(2026, 11, 19, 12, 50, tzinfo=IST)  # 10 minutes before a slot is too late to upload for it
    assert [d.strftime("%d %H:%M") for d in uploader.due_slots(late, set())] == ["19 19:00"]


def test_the_best_unposted_clips_are_picked_one_per_video():
    a, b = _video("a", created="2026-11-18T10:00:00"), _video("b", created="2026-11-19T10:00:00")
    clips = [(a, Clip(index=0, first_word=0, last_word=5, score=9)), (a, Clip(index=1, first_word=6, last_word=9, score=10)),
             (b, Clip(index=0, first_word=0, last_word=5, score=9)), (b, Clip(index=1, first_word=6, last_word=9, score=6)),
             (b, Clip(index=2, first_word=0, last_word=5, score=10, status="discarded"))]
    picked = uploader.pick(clips, done=set(), n=3)
    assert [(v.id, c.index) for v, c in picked] == [("a", 1), ("b", 0)]  # one per video, never below MIN_SCORE
    assert [(v.id, c.index) for v, c in uploader.pick(clips, done={"a/1"}, n=1)] == [("b", 0)]  # newest wins the tie


def test_shorts_metadata():
    clip = Clip(index=0, first_word=0, last_word=5, title="x" * 120, description="Chat lost it", hashtags="#gta6 #carryminati")
    meta = uploader.metadata(_video("a"), clip)
    assert len(meta["title"]) <= 100 and meta["title"].endswith("#Shorts")
    assert meta["tags"] == ["gta6", "carryminati"] and meta["categoryId"] == uploader.GAMING
    assert "#gta6 #carryminati #Shorts" in meta["description"] and "Credit: CarryisLive" in meta["description"]


def test_a_tick_renders_uploads_and_schedules_each_due_slot(tmp_path, monkeypatch):
    monkeypatch.setattr(uploader, "LOG_FILE", tmp_path / "uploads.json")
    a, b = _video("a"), _video("b")
    monkeypatch.setattr(uploader, "_candidates", lambda: [(a, Clip(index=0, first_word=0, last_word=5, score=9, title="One")),
                                                         (b, Clip(index=0, first_word=0, last_word=5, score=8, title="Two"))])
    monkeypatch.setattr(uploader.library, "media_for", lambda vid, clip: (tmp_path / "src.mp4", 0))
    monkeypatch.setattr(uploader.library, "clip_dir", lambda vid, i: tmp_path / vid)
    monkeypatch.setattr("app.plan.build_plan", lambda *a, **k: None)
    monkeypatch.setattr("app.cleanup.render_export", lambda *a, **k: True)
    sent = []
    monkeypatch.setattr(uploader, "upload", lambda path, meta, slot, log: sent.append((meta["title"], slot.hour)) or f"yt{len(sent)}")
    now = datetime(2026, 11, 19, 10, 0, tzinfo=IST)
    assert uploader.tick({}, lambda m: None, now=now) == 2
    assert sent == [("One #Shorts", 13), ("Two #Shorts", 19)]
    assert uploader.tick({}, lambda m: None, now=now) == 0  # both slots are filled now
    assert [e["youtube_id"] for e in uploader.history()] == ["yt1", "yt2"]
