import pytest

from app import sfx
from app.model import Clip, Video, Word
from app.plan import build_plan


def test_built_in_sounds_exist_for_every_automatic_role():
    roles = {s["role"] for s in sfx.list_sounds()}
    assert {"whoosh", "pop", "impact", "riser"} <= roles


def test_myinstants_pages_give_their_sound_link():
    html = '<button onclick="play(\'/media/sounds/vine-boom.mp3\', \'loader-1\')"></button>'
    assert sfx.sound_link(html) == "/media/sounds/vine-boom.mp3"
    assert sfx.sound_link("<p>nothing</p>") is None


@pytest.mark.parametrize("url", ["https://evil.example/a.mp3", "http://www.myinstants.com/en/instant/x/",
                                 "https://myinstants.com.evil.example/x", "file:///etc/passwd"])
def test_only_myinstants_links_are_fetched(url):
    with pytest.raises(ValueError, match="myinstants"):
        sfx.import_myinstants(url, "meme")


def test_effects_are_spaced_out_and_never_past_the_end():
    events = sfx.place([(3.0, 6.0), (4.0, 5.0), (9.0, 12.0)], [3.5, 15.0], meme_at=None, duration=15.2, seed=0)
    times = [e["t"] for e in events]
    assert times == sorted(times) and all(b - a >= sfx.MIN_GAP_S for a, b in zip(times, times[1:]))
    assert all(t < 15.2 - 0.3 for t in times)


def test_safe_only_skips_unsafe_sounds(tmp_path):
    sfx.list_sounds()
    sfx._save(sfx._load() + [{"name": "pop.wav", "role": "meme", "safe": False, "built_in": False}])
    assert sfx.pick("meme", 0, safe_only=True) is None


def test_plan_places_effects_only_when_switched_on():
    words = [Word(id=i, text=f"w{i}", display=f"w{i}", start=1 + i * 0.5, end=1.4 + i * 0.5) for i in range(60)]
    video = Video(id="v", title="t", duration=40, fps=25, width=1920, height=1080, words=words)
    on = build_plan(video, Clip(index=0, first_word=0, last_word=59, emoji_words={10: "🔥"}))
    off = build_plan(video, Clip(index=0, first_word=0, last_word=59, emoji_words={10: "🔥"}, sfx=False))
    assert on.sfx and not off.sfx
