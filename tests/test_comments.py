from app.comments import comment_note, mentions_in, parse_times


def test_timestamps_are_read_in_every_common_form():
    assert parse_times("32:10 this part 🔥 and also 1:05:09!", duration=5000) == [1930, 3909]
    assert parse_times("at 7:45-8:10 he says it", duration=5000) == [465, 490]


def test_times_beyond_the_video_and_non_times_are_ignored():
    assert parse_times("score was 3:1, see 99:59", duration=600) == []


def test_note_lists_the_most_mentioned_moments_and_top_comments():
    comments = [{"text": "12:30 gold", "likes": 50, "times": [750]}, {"text": "12:40 yes!!", "likes": 10, "times": [760]},
                {"text": "45:00 meh", "likes": 1, "times": [2700]}, {"text": "Best episode on liver health", "likes": 900, "times": []}]
    note = comment_note(comments)
    assert note.index("0:12:30") < note.index("0:45:00")  # the moment two comments point at comes first
    assert "Best episode on liver health" in note


def test_mentions_count_comments_pointing_into_a_range():
    comments = [{"times": [750]}, {"times": [760, 3000]}, {"times": []}]
    assert mentions_in(comments, 740, 800) == 2 and mentions_in(comments, 0, 100) == 0


def _words(text: str, start: float = 0.0, step: float = 0.5):
    from app.model import Word
    return [Word(id=i, text=w, display=w, start=start + i * step, end=start + i * step + step) for i, w in enumerate(text.split())]


def test_a_comment_quoting_a_line_points_at_where_it_was_said():
    from app.comments import keep_for_ai, match_quotes
    filler = " ".join(f"word{i}" for i in range(400))
    words = _words(f"{filler} sleep is the cheapest drug you will ever take so guard it {filler}")
    comments = [{"text": "\"sleep is the cheapest drug you will ever take\" hit hard", "likes": 300, "times": []},
                {"text": "great episode", "likes": 5, "times": []}]
    matched = match_quotes(comments, words)
    assert matched[0]["quoted"] == [200]          # word 400 at 0.5 s per word
    assert "quoted" not in matched[1]
    assert mentions_in(matched, 190, 210) == 1
    assert keep_for_ai(matched, top=0) == [matched[0]]  # quotes are kept even past the top-liked cut
