from app.model import Clip, Video, Word
from app.pacing import filler_cuts, tighten, zoom_spans
from app.plan import GAP_KEEP_S, build_plan


def make(texts, gaps=None):
    words, t = [], 1.0
    for i, text in enumerate(texts):
        words.append(Word(id=i, text=text, display=text, start=t, end=t + 0.3))
        t += 0.3 + (gaps or {}).get(i, 0.1)
    return Video(id="v", title="t", duration=t + 1, fps=25, width=1920, height=1080, words=words)


def test_fillers_and_english_stutters_are_cut_but_hindi_doubling_is_kept():
    video = make(["so", "um", "I", "I", "think", "jaldi", "jaldi", "uh,", "chalo", "end"])
    assert filler_cuts(video, Clip(index=0, first_word=0, last_word=9)) == [1, 2, 7]


def test_edge_words_are_never_cut_and_tighten_respects_the_switch():
    video = make(["um", "hello", "um"])
    assert filler_cuts(video, Clip(index=0, first_word=0, last_word=2)) == []
    off = Clip(index=0, first_word=0, last_word=4, tighten=False)
    assert tighten(make(["a", "um", "b", "c", "d"]), off) is off


def test_long_pauses_become_jump_cuts_keeping_a_breath():
    video = make(["one", "two", "three", "four"], gaps={1: 1.5})
    loose = build_plan(video, Clip(index=0, first_word=0, last_word=3, tighten=False, punch_in=False))
    tight = build_plan(video, Clip(index=0, first_word=0, last_word=3, punch_in=False))
    assert len(tight.segments) == 2 and len(loose.segments) == 1
    assert abs((loose.duration - tight.duration) - (1.5 - 2 * GAP_KEEP_S)) < 0.05


def test_punch_ins_flip_on_cuts_and_every_few_seconds():
    spans = zoom_spans(cut_times=[3.0], beat_times=[1.0, 6.0, 8.5, 11.0, 14.0], duration=16.0)
    assert spans[0][0] == 3.0
    flips = sorted({t for span in spans for t in span} - {16.0})
    assert all(b - a >= 2.5 for a, b in zip(flips, flips[1:]))


def test_every_framing_punches_in_except_the_post_template():
    video = make([f"w{i}" for i in range(40)], gaps={9: 1.0, 19: 1.0, 29: 1.0})
    for framing in ("crop", "fill", "split"):
        assert build_plan(video, Clip(index=0, first_word=0, last_word=39, framing=framing)).zooms
    assert not build_plan(video, Clip(index=0, first_word=0, last_word=39, template="tweet")).zooms


def test_a_long_stretch_without_beats_still_flips():
    spans = zoom_spans(cut_times=[], beat_times=[1.0, 14.0], duration=20.0)
    marks = sorted({0.0, 20.0} | {t for span in spans for t in span})
    assert max(b - a for a, b in zip(marks, marks[1:])) <= 5.5 + 1e-6
