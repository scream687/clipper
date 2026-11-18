from app.model import Clip, Video, Word
from app.moments import apply_edges, build_prompt, cold_open_end, find_clips, lead_with_question, moments_to_clips
from app.sentences import split_sentences
from app.youtube import has_word_timing


def sentence_words(n_sentences=20, words_per=8, word_s=0.5, pause=1.0):
    """n sentences of `words_per` words each; every sentence lasts 4 s, then a 1 s pause."""
    words, t, wid = [], 0.0, 0
    for _ in range(n_sentences):
        for k in range(words_per):
            words.append(Word(id=wid, text=f"w{wid}", display=f"w{wid}", start=t, end=t + word_s * 0.9)); wid += 1; t += word_s
        t += pause
    return words


def video(**kw):
    return Video(id="v", title="Title", channel="Chan", duration=200, fps=25, width=1920, height=1080,
                 words=sentence_words(), **kw)


def test_sentences_split_at_pauses_and_punctuation():
    words = [Word(id=0, text="Hello.", display="Hello.", start=0, end=0.3),
             Word(id=1, text="next", display="next", start=0.35, end=0.6),
             Word(id=2, text="one", display="one", start=0.65, end=0.9),
             Word(id=3, text="after", display="after", start=2.0, end=2.3)]
    assert [[w.id for w in s] for s in split_sentences(words)] == [[0], [1, 2], [3]]


def test_short_moment_grows_to_the_minimum_length():
    v = video(); sentences = split_sentences(v.words)
    clips = moments_to_clips(v, sentences, [{"first_sentence": 2, "last_sentence": 2, "title": "t", "hook": "h",
                                             "description": "d", "score": 8}], 20, 60, 30, "9:16")
    words = {w.id: w for w in v.words}
    assert words[clips[0].last_word].end - words[clips[0].first_word].start >= 20 - 1


def test_long_moment_shrinks_to_the_maximum_length():
    v = video(); sentences = split_sentences(v.words)
    clips = moments_to_clips(v, sentences, [{"first_sentence": 0, "last_sentence": 19, "title": "t", "hook": "h",
                                             "description": "d", "score": 8}], 20, 30, 30, "9:16")
    words = {w.id: w for w in v.words}
    assert words[clips[0].last_word].end - words[clips[0].first_word].start <= 30


def test_overlapping_and_invalid_moments_are_dropped_and_best_kept():
    v = video(); sentences = split_sentences(v.words)
    moments = [{"first_sentence": 4, "last_sentence": 8, "title": "weaker", "hook": "", "description": "", "score": 5},
               {"first_sentence": 5, "last_sentence": 9, "title": "stronger", "hook": "", "description": "", "score": 9},
               {"first_sentence": 99, "last_sentence": 120, "title": "bogus", "hook": "", "description": "", "score": 10}]
    clips = moments_to_clips(v, sentences, moments, 20, 60, 30, "9:16")
    assert [c.title for c in clips] == ["stronger"]


def test_clip_count_is_capped():
    v = video(); sentences = split_sentences(v.words)
    moments = [{"first_sentence": i, "last_sentence": i, "title": f"m{i}", "hook": "", "description": "", "score": 5}
               for i in range(0, 20, 5)]
    assert len(moments_to_clips(v, sentences, moments, 1, 60, 2, "9:16")) == 2


def test_prompt_carries_direction_replay_peaks_and_sentence_ids():
    heat = [{"start_time": i * 2.0, "end_time": (i + 1) * 2.0, "value": 0.9 if i == 50 else 0.1} for i in range(100)]
    v = video(heatmap=heat); sentences = split_sentences(v.words)
    prompt = build_prompt(v, sentences, 20, 60, 30, "only the pricing part")
    assert "only the pricing part" in prompt and "0:01:41" in prompt and "[s0 0:00:00]" in prompt and "20-60 seconds" in prompt


def test_find_clips_uses_the_given_ai_and_shape():
    v = video(shape="1:1")
    fake = lambda prompt, schema, system: {"moments": [{"first_sentence": 3, "last_sentence": 6, "title": "A",
                                                         "hook": "H", "description": "D", "score": 7}]}
    clips = find_clips(v, fake)
    assert len(clips) == 1 and clips[0].shape == "1:1" and clips[0].hook == "H"


def test_line_level_captions_are_detected():
    assert has_word_timing([{"word": "hello"}, {"word": "there"}])
    assert not has_word_timing([{"word": "hello there friend"}, {"word": "how are you"}])


def _clip(first, last, title="t"):
    from app.model import Clip
    return Clip(index=0, first_word=first, last_word=last, shape="9:16", title=title, hook="", description="", score=8)


def test_refined_edges_are_applied_inside_their_windows():
    v = video()  # 8 words per 5 s
    clips = [_clip(40, 79)]
    out = apply_edges(v, clips, {"clips": [{"clip": 0, "first_word": 36, "last_word": 83}]}, 20, 60)
    assert (out[0].first_word, out[0].last_word) == (36, 83)


def test_refined_edges_outside_window_too_short_or_overlapping_are_ignored():
    v = video()
    clips = [_clip(40, 79), _clip(90, 130)]
    bad = {"clips": [{"clip": 0, "first_word": 0, "last_word": 79},      # outside the start window
                     {"clip": 1, "first_word": 120, "last_word": 125}]}  # far too short
    assert [(c.first_word, c.last_word) for c in apply_edges(v, clips, bad, 20, 60)] == [(40, 79), (90, 130)]
    overlap = {"clips": [{"clip": 0, "first_word": 40, "last_word": 95}]}
    assert apply_edges(v, clips, overlap, 20, 60)[0].last_word == 79


def test_find_clips_survives_a_failed_edge_pass():
    from app.ai import AIUnavailable
    calls = []

    def ask(prompt, schema, system):
        calls.append(schema)
        if len(calls) == 2:
            raise AIUnavailable("limit")
        return {"moments": [{"first_sentence": 5, "last_sentence": 9, "title": "x", "hook": "h", "description": "d", "score": 8}]}

    clips = find_clips(video(), ask, 20, 60)
    assert len(clips) == 1 and len(calls) == 2


def test_long_unpunctuated_run_breaks_at_its_biggest_pause():
    words, t = [], 0.0
    for i in range(40):  # contiguous YouTube-style timings; word 22 hides a long pause
        d = 1.1 if i == 22 else 0.2
        words.append(Word(id=i, text="word", display="word", start=t, end=t + d)); t += d
    sentences = split_sentences(words)
    assert sentences[0][-1].id == 22


def test_hashtags_are_cleaned():
    from app.moments import clean_hashtags
    assert clean_hashtags("#Health, longevity #health #raj-shamani") == "#health #longevity #rajshamani"
    assert clean_hashtags("") == ""


def test_ending_pulls_in_the_hindi_auxiliary_but_not_into_the_next_clip():
    from app.moments import land_ending
    texts = ["wo", "sab", "solve", "kar", "sakte", "hain", "agar", "ye"]
    v = video().model_copy(update={"words": [Word(id=i, text=t, display=t, start=i, end=i + 1) for i, t in enumerate(texts)]})
    assert land_ending(v, _clip(0, 4), None).last_word == 5
    assert land_ending(v, _clip(0, 4), 5).last_word == 4
    assert land_ending(v, _clip(0, 6), None).last_word == 6


def test_prompt_carries_trends_and_the_users_taste_only_when_given():
    v = video()
    sentences = split_sentences(v.words)
    prompt = build_prompt(v, sentences, 20, 60, 30, "", trends="- GTA 6 / GTA VI: in 2 days (2026-11-19)",
                          taste="Rated high:\n- rated 9/10: \"Money talk\"")
    assert "GTA 6 / GTA VI: in 2 days" in prompt and "Never force a trend" in prompt
    assert "rated 9/10" in prompt
    plain = build_prompt(v, sentences, 20, 60, 30, "")
    assert "searching and watching" not in plain and "rated clips" not in plain


def test_long_transcripts_split_into_overlapping_parts_that_cover_everything():
    from app.moments import split_parts
    sentences = split_sentences(sentence_words(n_sentences=100))  # 100 sentences, 5 s apart = 500 s
    parts = split_parts(sentences, part_s=120, overlap_s=20)
    assert parts[0][0] == 0 and parts[-1][1] == 99
    assert all(b[0] <= a[1] for a, b in zip(parts, parts[1:]))  # every boundary overlaps
    assert len(split_parts(sentences, part_s=10_000)) == 1


def test_every_part_is_asked_and_the_ranking_pass_reorders(monkeypatch):
    from app import moments
    monkeypatch.setattr(moments, "PART_S", 120)
    v = Video(id="v", title="T", duration=500, fps=25, width=1920, height=1080, words=sentence_words(n_sentences=100))
    asked = []

    def ask(prompt, schema, system):
        asked.append(schema)
        if schema is moments.RANK_SCHEMA:
            return {"scores": [{"id": n, "score": 10 - n} for n in range(20)]}  # earlier candidates rank higher
        if schema is moments.EDGE_SCHEMA:
            return {"clips": []}
        first = int(prompt.split("[s", 1)[1].split(" ", 1)[0])  # first sentence id shown in this part
        return {"moments": [{"first_sentence": first + 1, "last_sentence": first + 6, "title": f"m{first}", "hook": "h",
                             "description": "d", "hashtags": "#a", "score": 5}]}

    clips = moments.find_clips(v, ask, min_s=20, max_s=40)
    parts = asked.count(moments.SCHEMA)
    assert parts >= 4 and asked.count(moments.RANK_SCHEMA) == 1
    assert [c.score for c in clips] == sorted((c.score for c in clips), reverse=True) and clips[0].score == 10


def _said(lines: list[str], t0: float = 0.0, gap: float = 6.0) -> list[list[Word]]:
    """One sentence per line, `gap` seconds apart, words 0.4 s each."""
    out, t = [], t0
    for line in lines:
        out.append([Word(id=0, text=w, display=w, start=t + k * 0.4, end=t + k * 0.4 + 0.35) for k, w in enumerate(line.split())])
        t += gap
    return out


TEASER = ["shame is the emotion that secretly controls people", "any emotion you avoid becomes your boss forever",
          "write fifteen ways to work out tonight", "smart kids stop trying when praised for brains",
          "a human scared of rejection rejects himself first"]
CHAT = ["please subscribe to our channel today", "this episode is sponsored by a laptop", "check the link below now",
        "welcome to the show joe", "thanks for having me raj"]


def _renumber(sentences):
    wid = 0
    for s in sentences:
        for w in s:
            w.id = wid; wid += 1
    return sentences


def test_cold_open_teaser_is_found_because_its_lines_are_said_again_later():
    sentences = _renumber(_said(TEASER) + _said(CHAT, t0=40) + _said(["filler talk number %d about many things" % i for i in range(20)], t0=80)
                          + _said(TEASER, t0=600))
    assert cold_open_end(sentences) == len(TEASER) - 1


def test_no_teaser_when_the_opening_is_not_repeated():
    sentences = _renumber(_said(CHAT) + _said(TEASER, t0=40) + _said(TEASER, t0=600))
    assert cold_open_end(sentences) == -1


def _clip_video(lines):
    sentences = _renumber(_said(lines, gap=4.0))
    words = [w for s in sentences for w in s]
    return Video(id="v", title="T", duration=200, fps=25, width=1920, height=1080, words=words), sentences


def test_clip_that_answers_a_question_starts_on_the_question():
    v, s = _clip_video(["we talked about food", "what are the future trends in health care?", "diagnostic toilets will read your health",
                        "every time you pee it tests you"])
    clip = Clip(index=0, first_word=s[2][0].id, last_word=s[3][-1].id)
    assert lead_with_question(v, clip, s, prev_last=None, max_s=60).first_word == s[1][0].id


def test_question_is_not_pulled_in_when_too_long_already_in_another_clip_or_not_a_question():
    v, s = _clip_video(["we talked about food", "what are the future trends in health care?", "diagnostic toilets will read your health",
                        "every time you pee it tests you"])
    clip = Clip(index=0, first_word=s[2][0].id, last_word=s[3][-1].id)
    assert lead_with_question(v, clip, s, prev_last=None, max_s=5).first_word == clip.first_word        # too long
    assert lead_with_question(v, clip, s, prev_last=s[1][-1].id, max_s=60).first_word == clip.first_word  # other clip
    v2, s2 = _clip_video(["we talked about food", "theek hai?", "diagnostic toilets will read your health"])
    tag = Clip(index=0, first_word=s2[2][0].id, last_word=s2[2][-1].id)
    assert lead_with_question(v2, tag, s2, prev_last=None, max_s=60).first_word == tag.first_word        # filler tag
    plain = Clip(index=0, first_word=s[1][0].id, last_word=s[3][-1].id)
    assert lead_with_question(v, plain, s, prev_last=None, max_s=60).first_word == plain.first_word     # food line
