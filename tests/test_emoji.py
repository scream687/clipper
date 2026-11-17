from app.emoji import emoji_png, is_emoji
from app.model import Clip, Video, Word
from app.plan import build_plan

WORDS = [Word(id=i, text=f"w{i}", display=f"w{i}", start=i * 0.5, end=i * 0.5 + 0.4) for i in range(20)]
VIDEO = Video(id="v", title="t", duration=20, fps=25, width=1920, height=1080, words=WORDS)


def test_emoji_appears_at_its_word_and_follows_cuts():
    clip = Clip(index=0, first_word=0, last_word=19, cut_word_ids=[2, 3], emoji_words={6: "💰", 15: "🔥"})
    plan = build_plan(VIDEO, clip)
    words = {w.word_id: w for w in plan.words}
    assert [e.emoji for e in plan.emojis] == ["💰", "🔥"]
    assert plan.emojis[0].start == words[6].start and plan.emojis[0].end <= plan.emojis[1].start


def test_emojis_on_cut_words_or_switched_off_are_dropped():
    assert build_plan(VIDEO, Clip(index=0, first_word=0, last_word=19, cut_word_ids=[6], emoji_words={6: "💰"})).emojis == []
    assert build_plan(VIDEO, Clip(index=0, first_word=0, last_word=19, emoji_words={6: "💰"}, show_emoji=False)).emojis == []


def test_only_real_emoji_are_accepted():
    assert is_emoji("💰") and is_emoji("👨‍⚕️") and is_emoji("❤️")
    assert not is_emoji("ab") and not is_emoji("💰💰") and not is_emoji("") and not is_emoji("{\\an8}")


def test_emoji_png_is_a_colour_image(tmp_path):
    path = emoji_png("🔥", 120, tmp_path)
    from PIL import Image
    img = Image.open(path)
    assert img.mode == "RGBA" and img.size == (120, 120) and img.getbbox() is not None
