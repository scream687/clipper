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
