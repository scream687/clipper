from app.model import Video, Word
from app.moments import build_prompt, find_clips, moments_to_clips
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
