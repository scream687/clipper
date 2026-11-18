"""Exports are rendered by HyperFrames (app/compose.py + app/render.py). These render for real, so they need Node."""

import json
import shutil
import subprocess

import pytest

from app import FFMPEG, FFPROBE, compose
from app.model import Clip, CropKey, Video, Word
from app.plan import build_plan
from app.render import render

FPS = 25.0
pytestmark = pytest.mark.skipif(shutil.which("npx") is None, reason="exports are rendered by HyperFrames, which needs Node")


@pytest.fixture(scope="module")
def source(tmp_path_factory):
    """12 s synthetic 1280x720 source whose red level encodes the frame number (frame n: red about 25 * (n % 10)),
    plus a 440 Hz tone, so a cut one frame off shows up."""
    path = tmp_path_factory.mktemp("src") / "source.mp4"
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=black:s=1280x720:r={int(FPS)}:d=12",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=12",
                    "-vf", "geq=r='25*mod(N\\,10)':g=40:b=40", "-c:v", "libx264", "-preset", "ultrafast", "-qp", "0",
                    "-pix_fmt", "yuv444p", "-g", "1", "-c:a", "aac", "-shortest", str(path)], check=True)
    return path


def probe(path):
    out = subprocess.run([FFPROBE, "-v", "error", "-count_frames", "-show_entries",
                          "stream=codec_type,width,height,nb_read_frames,duration", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    streams = json.loads(out)["streams"]
    return next(s for s in streams if s["codec_type"] == "video"), next(s for s in streams if s["codec_type"] == "audio")


def red_at(path, frame: int, x: int, y: int) -> int:
    raw = subprocess.run([FFMPEG, "-v", "error", "-i", str(path), "-vf", f"select='eq(n\\,{frame})',crop=8:8:{x}:{y},scale=1:1",
                          "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    return raw[0]


def video():
    words = [Word(id=i, text=f"word{i}", display=f"word{i}", start=1.0 + i * 0.5, end=1.0 + i * 0.5 + 0.4) for i in range(18)]
    return Video(id="v", title="t", channel="Chan", duration=12.0, fps=FPS, width=1280, height=720, words=words)


def assert_exact(plan, out):
    v, a = probe(out)
    assert (v["width"], v["height"]) == (plan.width, plan.height)
    assert int(v["nb_read_frames"]) == round(plan.duration * plan.fps)
    assert abs(float(a["duration"]) - plan.duration) < 0.06


def test_cuts_land_on_the_source_frames_within_one_frame(source, tmp_path):
    clip = Clip(index=0, first_word=0, last_word=17, cut_word_ids=[3, 4, 9, 14], hook="A hook", swear_filter=True,
                auto_keys=[CropKey(t=1.0, cx=0.1), CropKey(t=9.0, cx=0.9)], emoji_words={2: "🔥"}, emphasis_words=[6])
    plan = build_plan(video(), clip, handle="@me")
    out = render(plan, source, tmp_path / "out.mp4", log=lambda m: None)
    assert_exact(plan, out)
    codes = {n: red_at(source, n, 40, 400) for n in range(10)}  # decoded the same way: colour-range safe
    code = lambda v: min(codes, key=lambda n: abs(codes[n] - v))
    cursor = 0
    for seg in plan.segments:  # every kept run plays its own source frames (HyperFrames may be one frame late at a cut)
        n = seg.end_frame - seg.start_frame
        for i in (0, 1, n // 2, n - 2, n - 1):
            got, want = code(red_at(out, cursor + i, 40, 900)), (seg.start_frame + i) % 10
            assert (got - want) % 10 in (0, 1), (cursor + i, seg.start_frame + i, got)
        cursor += n


@pytest.mark.parametrize("shape,framing,background", [("1:1", "crop", "blur"), ("9:16", "fill", "gradient"), ("9:16", "split", "blur")])
def test_every_framing_renders_frame_exact(source, tmp_path, shape, framing, background):
    clip = Clip(index=0, first_word=0, last_word=12, cut_word_ids=[5], shape=shape, framing=framing, background=background,
                padding=6, radius=24, cam_box=[0.0, 0.0, 0.25, 0.3])
    plan = build_plan(video(), clip)
    assert_exact(plan, render(plan, source, tmp_path / "out.mp4", log=lambda m: None))


def test_music_effects_bleeps_logo_and_4k60_keep_exact_length(source, tmp_path, monkeypatch):
    from PIL import Image
    from app import assets
    monkeypatch.setattr(assets, "MUSIC_DIR", tmp_path / "music")
    monkeypatch.setattr(assets, "LOGO_FILE", tmp_path / "logo.png")
    (tmp_path / "music").mkdir()
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=220:duration=3",  # shorter than the clip: loops
                    str(tmp_path / "music" / "beat.mp3")], check=True)
    Image.new("RGBA", (300, 120), (255, 255, 255, 200)).save(assets.LOGO_FILE)
    words = [Word(id=i, text="fuck" if i == 4 else f"w{i}", display="fuck" if i == 4 else f"w{i}",
                  start=1.0 + i * 0.5, end=1.4 + i * 0.5) for i in range(18)]
    v = Video(id="v", title="t", channel="Chan", duration=12.0, fps=FPS, width=1280, height=720, words=words)
    clip = Clip(index=0, first_word=0, last_word=17, cut_word_ids=[9], swear_filter=True, music="beat.mp3", music_volume=20,
                music_peak=70, riser_s=3, show_logo=True, show_follow=True, emoji_words={3: "😂", 12: "💰"})
    plan = build_plan(v, clip, handle="@me", quality=2)
    assert plan.beeps and plan.music and plan.logo and plan.follow and plan.sfx
    out = render(plan, source, tmp_path / "x.mp4", log=lambda m: None, out_fps=60)
    vs, a = probe(out)
    assert (vs["width"], vs["height"]) == (2160, 3840)
    assert abs(int(vs["nb_read_frames"]) - round(plan.duration * 60)) <= 1 and abs(float(a["duration"]) - plan.duration) < 0.06


def test_inserts_cover_the_picture(source, tmp_path, monkeypatch):
    from app import inserts

    def red_clip(plan, src, start, folder, log):  # a solid red clip stands in for Remotion
        folder.mkdir(parents=True, exist_ok=True)
        out = folder / "red.mp4"
        subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=red:s=1080x1920:r={FPS}:d=3",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)], check=True)
        return [(i, out) for i in plan.inserts]
    monkeypatch.setattr(inserts, "render_all", red_clip)
    clip = Clip(index=0, first_word=0, last_word=17, broll=[{"first_word": 6, "last_word": 9, "kind": "card", "text": "9"}])
    plan = build_plan(video(), clip)
    out = render(plan, source, tmp_path / "ins.mp4", log=lambda m: None)
    assert_exact(plan, out)
    mid = round((plan.inserts[0].start + plan.inserts[0].end) / 2 * FPS)
    assert red_at(out, mid, 40, 40) > 200


def test_a_deleted_music_track_is_reported_clearly(source, tmp_path, monkeypatch):
    from app import assets
    monkeypatch.setattr(assets, "MUSIC_DIR", tmp_path / "music")
    plan = build_plan(video(), Clip(index=0, first_word=0, last_word=5, music="gone.mp3"))
    with pytest.raises(RuntimeError, match="no longer in the library"):
        render(plan, source, tmp_path / "x.mp4", log=lambda m: None)


def test_a_missing_source_is_reported_clearly(tmp_path):
    plan = build_plan(video(), Clip(index=0, first_word=0, last_word=3))
    with pytest.raises(RuntimeError, match="HyperFrames"):
        render(plan, tmp_path / "missing.mp4", tmp_path / "out.mp4", log=lambda m: None)
