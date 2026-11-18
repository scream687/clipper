"""The HyperFrames page, checked without rendering it."""

from app.compose import _minus, crop_states, music_audio, segment_times
from app.model import Clip, CropKey, Video, Word
from app.plan import build_plan


def video():
    words = [Word(id=i, text=f"w{i}", display=f"w{i}", start=1 + i * 0.5, end=1.4 + i * 0.5) for i in range(40)]
    return Video(id="v", title="t", duration=30, fps=25, width=1920, height=1080, words=words)


def test_segments_play_back_to_back_from_the_right_media_time():
    plan = build_plan(video(), Clip(index=0, first_word=0, last_word=20, cut_word_ids=[8], tighten=False))
    segs = segment_times(plan, frame_offset=25)
    assert segs[0][0] == 0 and abs(segs[1][0] - segs[0][1]) < 1e-9
    assert abs(segs[0][2] - (plan.segments[0].start_frame - 25) / 25) < 1e-9


def test_crop_states_follow_the_face_and_punch_in_around_the_anchor():
    clip = Clip(index=0, first_word=0, last_word=39, auto_keys=[CropKey(t=1, cx=0.2), CropKey(t=10, cx=0.8, jump=True)])
    plan = build_plan(video(), clip)
    states = crop_states(plan)
    assert states[0][0] == 0 and {s[3] for s in states} == {1.0, plan.zoom}
    k = plan.width / plan.crop_w
    t, x, y, z = states[0]
    left = min(max(0.2 * 1920 - plan.crop_w / 2, 0), 1920 - plan.crop_w)
    assert abs(x + left * k) < 0.02 and y == 0 and z == 1.0


def test_bleeps_cut_holes_in_the_speech():
    assert _minus(0, 10, [(2, 3), (5, 6)]) == [(0, 2), (3, 5), (6, 10)]
    assert _minus(0, 1, [(0, 1)]) == []


def test_music_ducks_under_speech_and_loops():
    plan = build_plan(video(), Clip(index=0, first_word=0, last_word=39, music="m.mp3", music_volume=40))
    page = music_audio(plan, length=4.0, track=1)
    volumes = [float(part.split('"')[0]) for part in page.split('data-volume="')[1:]]
    assert min(volumes) < 0.4 * 0.5 and max(volumes) <= 0.4 + 1e-9
    assert 'data-media-start="0.000000"' in page.split("</audio>", 9)[8]  # the 4 s track starts again after 8 steps
