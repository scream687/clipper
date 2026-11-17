import json
import subprocess
import zipfile
from datetime import datetime, timedelta

import pytest

from app import FFMPEG, FFPROBE, cleanup, library
from app.model import Clip, Video, Word
from app.plan import build_plan
from app.render import render

FPS = 25.0


@pytest.fixture
def lib(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "VIDEOS", tmp_path / "videos")
    return tmp_path


def make_video(created_days_ago: float) -> Video:
    words = [Word(id=i, text=f"w{i}", display=f"w{i}", start=2.0 + i * 0.5, end=2.0 + i * 0.5 + 0.4) for i in range(30)]
    created = (datetime.now() - timedelta(days=created_days_ago)).isoformat(timespec="seconds")
    video = Video(id="20261101-120000-test", title="T", channel="C", duration=20, fps=FPS, width=1280, height=720,
                  words=words, stage="ready", created_at=created)
    library.save_video(video)
    folder = library.video_dir(video.id)
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size=1280x720:rate={int(FPS)}:duration=20",
                    "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=48000:duration=20", "-c:v", "libx264",
                    "-preset", "ultrafast", "-g", "12", "-c:a", "aac", "-shortest", str(folder / "source.mp4")], check=True)
    (folder / "proxy.mp4").write_bytes(b"proxy")
    library.save_clip(video.id, Clip(index=0, first_word=4, last_word=12, cut_word_ids=[6]))
    library.save_clip(video.id, Clip(index=1, first_word=15, last_word=25, status="discarded"))
    return video


def frames(path):
    out = subprocess.run([FFPROBE, "-v", "error", "-count_frames", "-select_streams", "v", "-show_entries",
                          "stream=nb_read_frames", "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout
    return int(json.loads(out)["streams"][0]["nb_read_frames"])


def test_recent_videos_are_left_alone(lib):
    video = make_video(created_days_ago=3)
    assert cleanup.run_cleanup() == 0
    assert (library.video_dir(video.id) / "source.mp4").exists()


def test_old_videos_keep_a_window_per_clip_and_lose_the_full_files(lib):
    video = make_video(created_days_ago=15)

    assert cleanup.run_cleanup() == 1

    folder = library.video_dir(video.id)
    assert not (folder / "source.mp4").exists() and not (folder / "proxy.mp4").exists()
    for clip in library.load_clips(video.id):
        assert clip.mezz_start_frame is not None and (library.clip_dir(video.id, clip.index) / "mezz.mp4").exists()
        span = clip.mezz_end_frame - clip.mezz_start_frame
        assert frames(library.clip_dir(video.id, clip.index) / "mezz.mp4") == span


def test_a_clip_still_renders_frame_exactly_from_its_window(lib, tmp_path):
    video = make_video(created_days_ago=15)
    clip = library.load_clip(video.id, 0)
    plan = build_plan(video, clip)
    before = render(plan, library.video_dir(video.id) / "source.mp4", tmp_path / "before.mp4", log=lambda m: None)

    cleanup.run_cleanup()
    clip = library.load_clip(video.id, 0)
    source, offset = library.media_for(video.id, clip)
    after = render(plan, source, tmp_path / "after.mp4", log=lambda m: None, frame_offset=offset)

    assert frames(before) == frames(after) == round(plan.duration * FPS)


def test_unfinished_videos_are_resumed_on_start(lib, monkeypatch):
    for stage, vid in [("Downloading video", "20261101-120000-a"), ("ready", "20261101-120000-b"), ("failed", "20261101-120000-c")]:
        library.save_video(Video(id=vid, title=vid, duration=0, fps=0, width=0, height=0, stage=stage))
    submitted = []
    monkeypatch.setattr(cleanup.queue, "submit", lambda lane, kind, subject, work: submitted.append(subject) or "job")

    cleanup.resume_unfinished(lambda vid: (lambda log: None))

    assert submitted == ["20261101-120000-a"]


def test_export_all_zips_every_clip_that_is_not_discarded(lib, tmp_path):
    video = make_video(created_days_ago=1)

    path = cleanup.export_all(video.id, handle="", log=lambda m: None)

    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        assert len(names) == 1 and names[0].endswith(".mp4")
        assert all(info.compress_type == zipfile.ZIP_STORED for info in z.infolist())


def test_temp_files_are_unique_per_process_and_stale_ones_are_swept(tmp_path):
    import os
    from app.media import partial_path, remove_stale_partials
    out = tmp_path / "proxy.mp4"
    mine = partial_path(out)
    assert str(os.getpid()) in mine.name and mine != out
    stale = tmp_path / "proxy.partial-1.mp4"
    stale.write_bytes(b"x"); mine.write_bytes(b"y")

    remove_stale_partials(tmp_path)

    assert not stale.exists() and mine.exists()
