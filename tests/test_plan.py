from app.model import Clip, CropKey, Video, Word
from app.plan import build_plan

FPS = 25.0


def words(n, gap=0.5, start=10.0):
    """n words, each 0.4 s long, one every `gap` seconds, starting at `start`."""
    return [Word(id=i, text=f"w{i}", display=f"w{i}", start=start + i * gap, end=start + i * gap + 0.4) for i in range(n)]


def video(n=40, **kw):
    return Video(id="v", title="t", channel="c", duration=600.0, fps=FPS, width=1920, height=1080, words=words(n), **kw)


def test_plain_clip_keeps_one_segment_and_starts_at_zero():
    # Arrange: words 0..9 span 10.0 .. 14.9
    clip = Clip(index=0, first_word=0, last_word=9)

    # Act
    plan = build_plan(video(), clip)

    # Assert
    assert len(plan.segments) == 1
    seg = plan.segments[0]
    assert seg.start_frame == round(10.0 * FPS) and seg.end_frame == round(14.9 * FPS)
    assert plan.words[0].start == 0.0
    assert plan.duration == (seg.end_frame - seg.start_frame) / FPS


def test_deleting_words_shifts_later_words_earlier_by_the_cut_length():
    clip = Clip(index=0, first_word=0, last_word=9, cut_word_ids=[3, 4])

    plan = build_plan(video(), clip)

    # Two segments: before word 3, and from word 5 on
    assert len(plan.segments) == 2
    removed = plan_without_cuts_duration(clip) - plan.duration
    assert 0.9 < removed < 1.1  # two words, 0.5 s apart each
    shown = [w.word_id for w in plan.words]
    assert 3 not in shown and 4 not in shown
    w5 = next(w for w in plan.words if w.word_id == 5)
    assert abs(w5.start - (12.5 - 10.0 - removed)) <= 1 / FPS


def plan_without_cuts_duration(clip):
    return build_plan(video(), clip.model_copy(update={"cut_word_ids": []})).duration


def test_text_fix_replaces_display_but_keeps_timing():
    plan = build_plan(video(), Clip(index=0, first_word=0, last_word=4, text_fixes={2: "fixed"}))

    w2 = next(w for w in plan.words if w.word_id == 2)
    assert w2.text == "fixed"
    assert abs(w2.start - 1.0) <= 1 / FPS


def test_segments_are_frame_aligned_and_never_empty():
    plan = build_plan(video(), Clip(index=0, first_word=0, last_word=9, cut_word_ids=[1, 5, 8]))

    for seg in plan.segments:
        assert seg.end_frame > seg.start_frame
    out = sum(s.end_frame - s.start_frame for s in plan.segments) / FPS
    assert abs(out - plan.duration) < 1e-9


def test_crop_keys_move_with_cuts_and_drop_inside_cuts():
    keys = [CropKey(t=10.0, cx=0.2), CropKey(t=11.6, cx=0.9), CropKey(t=13.0, cx=0.6)]  # 11.6 sits inside cut words 3-4
    clip = Clip(index=0, first_word=0, last_word=9, cut_word_ids=[3, 4], auto_keys=keys)

    plan = build_plan(video(), clip)

    times = [round(k.t, 2) for k in plan.crop_keys]
    assert 0.0 in times
    assert all(abs(k.cx - 0.9) > 1e-6 for k in plan.crop_keys)  # the key inside the cut is gone
    later = next(k for k in plan.crop_keys if abs(k.cx - 0.6) < 1e-6)
    assert later.t < 3.0  # 13.0 s source minus 10.0 s start minus about 1 s cut


def test_manual_key_overrides_auto_from_its_time_onwards():
    clip = Clip(index=0, first_word=0, last_word=9,
                auto_keys=[CropKey(t=10.0, cx=0.2), CropKey(t=12.0, cx=0.3)],
                manual_keys=[CropKey(t=11.0, cx=0.8)])

    plan = build_plan(video(), clip)

    assert plan.crop_at(0.5) == 0.2
    assert plan.crop_at(1.5) == 0.8
    assert plan.crop_at(4.0) == 0.8  # manual position holds; later auto keys are ignored


def test_default_crop_is_centred_when_there_are_no_keys():
    assert build_plan(video(), Clip(index=0, first_word=0, last_word=3)).crop_at(1.0) == 0.5


def test_captions_break_into_lines_that_fit_the_frame():
    long_words = [Word(id=i, text=w, display=w, start=i * 0.4, end=i * 0.4 + 0.35)
                  for i, w in enumerate("KHAREEDNE PROPERTY INVESTMENT OPPORTUNITIES EVERYWHERE TODAY".split())]
    v = Video(id="v", title="t", channel="c", duration=60.0, fps=FPS, width=1920, height=1080, words=long_words)

    plan = build_plan(v, Clip(index=0, first_word=0, last_word=5, look="highlight"))

    for group in plan.captions:
        for line in group.lines:
            assert line.width_px <= plan.safe_width_px
    assert sum(len(line.word_ids) for g in plan.captions for line in g.lines) == 6


def test_shape_sets_output_size():
    v = video()
    sizes = {s: (build_plan(v, Clip(index=0, first_word=0, last_word=3, shape=s)).width,
                 build_plan(v, Clip(index=0, first_word=0, last_word=3, shape=s)).height)
             for s in ("9:16", "1:1", "16:9")}
    assert sizes == {"9:16": (1080, 1920), "1:1": (1080, 1080), "16:9": (1920, 1080)}


def test_swear_words_are_starred_and_give_beep_intervals():
    ws = [Word(id=0, text="this", display="this", start=0.0, end=0.4),
          Word(id=1, text="fucking", display="fucking", start=0.5, end=0.9),
          Word(id=2, text="rocks", display="rocks", start=1.0, end=1.4)]
    v = Video(id="v", title="t", channel="c", duration=10.0, fps=FPS, width=1920, height=1080, words=ws)

    plan = build_plan(v, Clip(index=0, first_word=0, last_word=2, swear_filter=True))

    shown = {w.word_id: w.text for w in plan.words}
    assert shown[1].startswith("f") and set(shown[1][1:]) == {"*"}
    assert len(plan.beeps) == 1 and plan.beeps[0][0] < 0.5 < plan.beeps[0][1]


def test_swear_filter_off_leaves_words_alone():
    ws = [Word(id=0, text="fucking", display="fucking", start=0.0, end=0.4)]
    v = Video(id="v", title="t", channel="c", duration=10.0, fps=FPS, width=1920, height=1080, words=ws)

    plan = build_plan(v, Clip(index=0, first_word=0, last_word=0))

    assert plan.words[0].text == "fucking" and plan.beeps == []


def test_long_hook_shrinks_to_fit_instead_of_losing_words():
    hook = "I don't think non-procrastinators exist. All of you are procrastinators, every single one of you here."
    plan = build_plan(video(), Clip(index=0, first_word=0, last_word=3, hook=hook))

    assert " ".join(plan.hook.lines) == hook
    assert len(plan.hook.lines) <= 3


def test_fit_keys_become_output_time_whole_frame_spans():
    # Whole-frame span starts at 10.7 s, inside cut words 1-2 (10.5-11.4), and ends at the 12.0 s key.
    keys = [CropKey(t=10.0, cx=0.5), CropKey(t=10.7, cx=0.5, jump=True, fit=True), CropKey(t=12.0, cx=0.3, jump=True)]
    clip = Clip(index=0, first_word=0, last_word=9, cut_word_ids=[1, 2], auto_keys=keys, framing="crop")

    plan = build_plan(video(), clip)

    # The span starts where the second segment does (it begins inside the span) and ends at the 12.0 s key.
    first, second = plan.segments
    seg2_out = (first.end_frame - first.start_frame) / FPS
    expected = (seg2_out, seg2_out + 12.0 - second.start_frame / FPS)
    assert [(round(a, 3), round(b, 3)) for a, b in plan.fit_spans] == [tuple(round(v, 3) for v in expected)]
