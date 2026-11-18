from app.model import Clip, Video, Word
from app.plan import build_plan
from app.template import tweet_header


def _video():
    words = [Word(id=i, text=f"w{i}", display=f"w{i}", start=i, end=i + 0.5) for i in range(6)]
    return Video(id="v", title="t", channel="Raj Shamani", duration=10, fps=25, width=1920, height=1080, words=words)


def test_tweet_template_puts_the_post_on_top_and_the_video_card_under_it():
    clip = Clip(index=0, first_word=0, last_word=5, template="tweet", hook="This changed how I think about health forever")
    plan = build_plan(_video(), clip, handle="@sfxme", brand="SFX Me")
    t, c = plan.tweet, plan.canvas
    assert plan.framing == "fill" and plan.hook is None                 # the hook is the post text now
    assert t["name"] == "SFX Me" and t["handle"] == "@sfxme" and t["lines"]
    assert c["card_y"] == t["y"] + t["height"] and c["background"] == "color" and c["color"] == "#FFFFFF"
    above, below = t["y"], plan.height - (plan.caption_y * plan.height + plan.style["line_px"] * plan.style["max_lines"] / 2)
    assert abs(above - below) < 60  # post, video and captions sit centred
    assert plan.caption_y * plan.height > c["card_y"] + c["card_h"]


def test_dark_tweet_and_the_header_image_matches_the_plan():
    clip = Clip(index=0, first_word=0, last_word=5, template="tweet", tweet_theme="dark", hook="Short one")
    plan = build_plan(_video(), clip, handle="@sfxme")
    assert plan.canvas["color"] == "#000000" and plan.tweet["name"] == "sfxme"
    image = tweet_header(plan)
    assert image.size == (plan.width, plan.tweet["height"]) and image.getpixel((5, 5))[:3] == (0, 0, 0)


def test_light_post_uses_dark_captions_with_a_blue_highlight_right_under_the_video():
    plan = build_plan(_video(), Clip(index=0, first_word=0, last_word=5, template="tweet", hook="Hi"), handle="@me")
    assert (plan.style["color"], plan.style["active"], plan.style["outline"]) == ("#0F1419", "#1D9BF0", 0)
    assert plan.style["handle"]["color"] == "#536471"
    c = plan.canvas
    top_of_captions = plan.caption_y * plan.height - plan.style["line_px"] * plan.style["max_lines"] / 2
    assert c["card_y"] + c["card_h"] <= top_of_captions <= c["card_y"] + c["card_h"] + 80
    boxed = build_plan(_video(), Clip(index=0, first_word=0, last_word=5, template="tweet", look="boxed", hook="Hi"))
    assert boxed.style["color"] == "#FFFFFF"  # white on its own dark box
