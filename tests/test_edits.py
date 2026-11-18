from app import edits, pexels
from app.ai import AIUnavailable
from app.model import Clip, Video, Word
from app.plan import INSERT_NOT_BEFORE_S, build_plan


def video():
    words = [Word(id=i, text=f"w{i}", display=f"w{i}", start=1 + i * 0.4, end=1.3 + i * 0.4) for i in range(80)]
    return Video(id="v", title="t", duration=40, fps=25, width=1920, height=1080, words=words)


def test_ai_picks_are_kept_only_inside_the_clip():
    clip = Clip(index=0, first_word=10, last_word=40, cut_word_ids=[15])
    answer = {"clips": [{"clip": 0, "emphasis": [12, 15, 99], "punchline": 30,
                         "inserts": [{"first_word": 20, "last_word": 25, "kind": "card", "text": "90%", "sub": "of people", "query": ""},
                                     {"first_word": 5, "last_word": 8, "kind": "stock", "text": "x", "sub": "", "query": "y"}]}]}
    [out] = edits.apply([clip], answer)
    assert out.emphasis_words == [12] and out.meme_word == 30
    assert [b["text"] for b in out.broll] == ["90%"]


def test_ai_trouble_leaves_clips_working():
    def down(prompt, schema, system):
        raise AIUnavailable("limit")
    clip = Clip(index=0, first_word=1, last_word=5)
    assert edits.decorate(video(), [clip], down) == [clip]


def test_inserts_land_on_kept_words_outside_the_opening_and_never_overlap():
    broll = [{"first_word": 0, "last_word": 3, "kind": "card", "text": "early"},
             {"first_word": 20, "last_word": 22, "kind": "card", "text": "a"},
             {"first_word": 21, "last_word": 30, "kind": "stock", "text": "b", "query": "q"},
             {"first_word": 50, "last_word": 51, "kind": "stock", "text": "c", "query": "q"}]
    plan = build_plan(video(), Clip(index=0, first_word=0, last_word=79, broll=broll))
    assert [i.text for i in plan.inserts] == ["a", "c"]
    assert all(i.start >= INSERT_NOT_BEFORE_S and 1.0 <= i.end - i.start <= 3.5 for i in plan.inserts)
    assert not build_plan(video(), Clip(index=0, first_word=0, last_word=79, broll=broll, show_broll=False)).inserts


def test_cutaways_get_a_whoosh():
    broll = [{"first_word": 20, "last_word": 25, "kind": "card", "text": "a"}]
    plan = build_plan(video(), Clip(index=0, first_word=0, last_word=79, broll=broll, punch_in=False))
    assert any(abs(e["t"] - (plan.inserts[0].start - 0.25)) < 1e-6 for e in plan.sfx)


def test_pexels_picks_a_portrait_mp4_near_full_hd():
    files = [{"file_type": "video/mp4", "link": "https://a", "width": 1920, "height": 1080},
             {"file_type": "video/mp4", "link": "https://b", "width": 720, "height": 1280},
             {"file_type": "video/mp4", "link": "https://c", "width": 1080, "height": 1920}]
    assert pexels.best_video_file({"video_files": files}) == "https://c"
    assert pexels.best_video_file({"video_files": files[:1]}) is None


def test_no_cutaways_over_gameplay_but_emphasis_stays():
    answer = {"clips": [{"clip": 0, "emphasis": [12], "punchline": -1,
                         "inserts": [{"first_word": 20, "last_word": 25, "kind": "card", "text": "LOOTING", "sub": "", "query": ""}]}]}
    clip = Clip(index=0, first_word=10, last_word=40)
    [game] = edits.decorate(video().model_copy(update={"content": "stream"}), [clip], lambda *a: answer)
    [talk] = edits.decorate(video().model_copy(update={"content": "talk"}), [clip], lambda *a: answer)
    assert game.broll == [] and game.emphasis_words == [12]
    assert [b["text"] for b in talk.broll] == ["LOOTING"]
