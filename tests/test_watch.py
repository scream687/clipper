import json
import time

from app import library, watch
from app.model import Video


def test_capture_reaches_back_before_the_moment_within_limits():
    assert watch.capture_window(1000, 900) == 100 + watch.BEFORE_S
    assert watch.capture_window(1000, 999) == 120   # at least two minutes
    assert watch.capture_window(1000, 0) == 300     # never more than five


def test_tail_returns_only_complete_new_lines(tmp_path):
    f = tmp_path / "chat.json.part"
    tail = watch._Tail([f, tmp_path / "chat.json"])
    assert tail.lines() == []
    f.write_text('{"a": 1}\n{"b"')
    assert tail.lines() == ['{"a": 1}']
    with open(f, "a") as out:
        out.write(': 2}\n')
    assert tail.lines() == ['{"b": 2}']


def _line(t, author, text):
    return json.dumps({"replayChatItemAction": {"actions": [{"addChatItemAction": {"item": {"liveChatTextMessageRenderer": {
        "message": {"runs": [{"text": text}]}, "timestampUsec": str(int(t * 1e6)), "authorExternalChannelId": author}}}}]}})


def _run(tmp_path, monkeypatch, age_s):
    """watch() over a fake chat whose burst happened age_s ago; returns (result, captured ids, burst time)."""
    monkeypatch.setattr(library, "VIDEOS", tmp_path)
    for name, value in {"POLL_S": 0, "WARMUP_S": 0}.items():
        monkeypatch.setattr(watch, name, value)
    parent = Video(id="20260101-000000-stream", url="https://youtu.be/x", title="Stream", duration=0, fps=0,
                   width=0, height=0, content="stream")
    library.save_video(parent)
    now = time.time()
    burst = now - age_s
    lines = [_line(t, f"u{t}", "kab tak stream") for t in range(int(now - 800), int(now - 60), 3)]
    lines += [_line(burst + k * 0.2, f"fan{k}", "😂😂 LOL") for k in range(40)]

    class FakeChat:  # yt-dlp following the chat: the file is there, then the stream ends
        def __init__(self, command, **kw):
            (library.video_dir(parent.id) / "work" / "watch.live_chat.json.part").write_text("\n".join(lines) + "\n")
            self.polls = 0
        def poll(self):
            self.polls += 1
            return None if self.polls < 2 else 0
        def terminate(self):
            pass

    monkeypatch.setattr(watch.subprocess, "Popen", FakeChat)
    monkeypatch.setattr(watch, "fetch_info", lambda url: {"is_live": True, "title": "Stream", "channel": "C"})
    monkeypatch.setattr(watch, "download_live_window", lambda info, out, window, log: out.write_bytes(b""))
    captured = []
    result = watch.watch(parent.id, captured.append, log=lambda m: None)
    return result, captured, burst


def test_an_old_spike_is_not_captured_late(tmp_path, monkeypatch):
    assert _run(tmp_path, monkeypatch, age_s=200)[0] == {"captured": 0}


def test_a_chat_explosion_is_captured_with_the_moment_marked(tmp_path, monkeypatch):
    result, captured, burst = _run(tmp_path, monkeypatch, age_s=70)  # settled, and fresh
    assert result == {"captured": 1} and not watch.is_watching("20260101-000000-stream")
    assert watch.to_resume() == []  # the stream ended: nothing to pick up after a restart
    child = library.load_video(captured[0])
    [peak] = child.chat_peaks
    # the moment is the burst minus chat's typing delay, as a time inside the capture
    assert abs((child.duration - peak["time"]) - (time.time() - (burst - watch.CHAT_LAG_S))) < 5
    assert child.options["max_clips"] == watch.CLIPS_PER_SPIKE and child.options["live"] and child.channel == "C"


def test_a_watch_cut_off_by_a_restart_is_resumed(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "VIDEOS", tmp_path)
    library.video_dir("20260101-000000-live").mkdir(parents=True)
    watch._mark("20260101-000000-live", True)
    assert watch.to_resume() == ["20260101-000000-live"]
