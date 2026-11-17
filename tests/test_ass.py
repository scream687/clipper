import re

from app.ass import build_ass
from app.model import Clip, Video, Word
from app.plan import build_plan

FPS = 25.0
WORDS = [Word(id=i, text=w, display=w, start=i * 0.5, end=i * 0.5 + 0.4)
         for i, w in enumerate("this is the moment {that} changed everything for me".split())]
VIDEO = Video(id="v", title="t", channel="Some Channel", duration=60.0, fps=FPS, width=1920, height=1080, words=WORDS)


def ass_for(**clip_fields):
    clip = Clip(index=0, first_word=0, last_word=len(WORDS) - 1, **clip_fields)
    plan = build_plan(VIDEO, clip, handle="@me")
    return plan, build_ass(plan)


def dialogues(text, style=None):
    lines = [line for line in text.splitlines() if line.startswith("Dialogue:")]
    return [line for line in lines if style is None or f",{style}," in line]


def test_canvas_matches_the_output_size():
    for shape, size in {"9:16": "1080 1920", "1:1": "1080 1080", "16:9": "1920 1080"}.items():
        _, ass = ass_for(shape=shape)
        x, y = size.split()
        assert f"PlayResX: {x}" in ass and f"PlayResY: {y}" in ass


def test_highlight_has_one_event_per_word_with_one_word_coloured():
    plan, ass = ass_for(look="highlight")
    events = dialogues(ass, "Caption")
    assert len(events) == len(plan.words)
    assert all(e.count("\\c&H") == 2 for e in events)  # switch to the highlight colour and back


def test_karaoke_uses_fill_tags_whose_total_matches_the_group():
    plan, ass = ass_for(look="karaoke")
    events = dialogues(ass, "Caption")
    assert len(events) == len(plan.captions)
    first = events[0]
    centis = sum(int(n) for n in re.findall(r"\\kf(\d+)", first))
    group = plan.captions[0]
    assert abs(centis / 100 - (group.end - group.start)) < 0.05


def test_pop_scales_each_word_in():
    _, ass = ass_for(look="pop")
    assert all("\\t(0," in e and "\\fscx" in e for e in dialogues(ass, "Caption"))


def test_boxed_uses_an_opaque_box_and_keeps_case():
    _, ass = ass_for(look="boxed")
    style = next(line for line in ass.splitlines() if line.startswith("Style: Caption"))
    assert style.split(",")[15] == "3"  # BorderStyle
    assert "this is the" in ass.lower() and "THIS IS THE" not in ass


def test_uppercase_looks_are_uppercased():
    _, ass = ass_for(look="highlight")
    assert "MOMENT" in ass


def test_braces_in_words_cannot_inject_override_tags():
    _, ass = ass_for(look="karaoke")
    assert "{that}" not in ass and "{THAT}" not in ass


def test_hook_handle_and_credit_appear_when_on():
    _, ass = ass_for(hook="He changed everything")
    assert dialogues(ass, "Hook") and "@me" in ass and "Credit: Some Channel" in ass
    hook = dialogues(ass, "Hook")[0]
    assert hook.split(",")[1] == "0:00:00.00" and hook.split(",")[2] == "0:00:04.00"


def test_hook_handle_and_credit_can_be_switched_off():
    _, ass = ass_for(hook="He changed everything", show_hook=False, show_handle=False, show_credit=False)
    assert not dialogues(ass, "Hook") and not dialogues(ass, "Handle") and not dialogues(ass, "Credit")
