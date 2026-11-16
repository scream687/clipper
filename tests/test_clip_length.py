from yt_short_clipper_core import prompts
from yt_short_clipper_core.highlight_finder import _validate_highlights


def highlight(start, end):
    return {"start_time": start, "end_time": end, "title": "t", "hook_text": "h"}


def quiet(_message):
    pass


def test_short_clip_is_accepted_when_limits_allow_it():
    # Arrange: a 25 second pick
    picks = [highlight("00:01:00,000", "00:01:25,000")]

    # Act
    valid = _validate_highlights(picks, 5, quiet, min_seconds=20, max_seconds=60)

    # Assert
    assert len(valid) == 1


def test_short_clip_is_rejected_at_the_core_defaults():
    picks = [highlight("00:01:00,000", "00:01:25,000")]

    assert _validate_highlights(picks, 5, quiet) == []


def test_clip_over_the_maximum_is_rejected():
    picks = [highlight("00:01:00,000", "00:02:10,000")]

    assert _validate_highlights(picks, 5, quiet, min_seconds=20, max_seconds=60) == []


def test_exact_range_from_the_direction_ignores_the_limits():
    picks = [highlight("00:01:00,000", "00:02:10,000")]

    valid = _validate_highlights(
        picks, 5, quiet, [(60.0, 130.0)], min_seconds=20, max_seconds=60
    )

    assert len(valid) == 1


def test_prompt_templates_carry_no_hard_coded_clip_length():
    for template in (
        prompts.DEFAULT_HIGHLIGHT_PROMPT,
        prompts.USER_DIRECTION_BLOCK,
        prompts.USER_DIRECTION_REMINDER,
    ):
        assert "60-120" not in template
        assert "85-95" not in template
