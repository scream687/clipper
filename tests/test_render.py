import json
import subprocess

import pytest

from app import FFMPEG, FFPROBE
from app.model import Clip, CropKey, Video, Word
from app.plan import build_plan
from app.render import render

FPS = 25.0


@pytest.fixture(scope="module")
def source(tmp_path_factory):
    """12 s synthetic 1280x720 source: moving test pattern plus a 440 Hz tone."""
    path = tmp_path_factory.mktemp("src") / "source.mp4"
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size=1280x720:rate={int(FPS)}:duration=12",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=12",
                    "-c:v", "libx264", "-preset", "ultrafast", "-g", "12", "-c:a", "aac", "-shortest", str(path)], check=True)
    return path


def probe(path):
    out = subprocess.run([FFPROBE, "-v", "error", "-count_frames", "-show_entries",
                          "stream=codec_type,width,height,nb_read_frames,duration", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    streams = json.loads(out)["streams"]
    return next(s for s in streams if s["codec_type"] == "video"), next(s for s in streams if s["codec_type"] == "audio")


def video():
    words = [Word(id=i, text=f"word{i}", display=f"word{i}", start=1.0 + i * 0.5, end=1.0 + i * 0.5 + 0.4) for i in range(18)]
    return Video(id="v", title="t", channel="Chan", duration=12.0, fps=FPS, width=1280, height=720, words=words)


@pytest.mark.parametrize("shape,framing", [("9:16", "crop"), ("1:1", "crop"), ("16:9", "crop"), ("9:16", "fill")])
def test_render_matches_the_plan_exactly(source, tmp_path, shape, framing):
    # Arrange: three cuts, a moving crop, captions, hook, handle, credit, swear filter on
    clip = Clip(index=0, first_word=0, last_word=17, cut_word_ids=[3, 4, 9, 14], shape=shape, framing=framing,
                auto_keys=[CropKey(t=1.0, cx=0.1), CropKey(t=9.0, cx=0.9)], hook="A hook line", swear_filter=True)
    plan = build_plan(video(), clip, handle="@me")

    # Act
    out = render(plan, source, tmp_path / "out.mp4", log=lambda m: None)

    # Assert
    v, a = probe(out)
    assert (v["width"], v["height"]) == (plan.width, plan.height)
    assert int(v["nb_read_frames"]) == round(plan.duration * FPS)
    assert abs(float(a["duration"]) - plan.duration) < 0.03


def test_render_reports_ffmpeg_failure_clearly(tmp_path):
    plan = build_plan(video(), Clip(index=0, first_word=0, last_word=3))
    with pytest.raises(RuntimeError, match="ffmpeg"):
        render(plan, tmp_path / "missing.mp4", tmp_path / "out.mp4", log=lambda m: None)
