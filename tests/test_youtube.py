from app.youtube import find_orig_caption_code

DUBBED = {"ar-orig": [], "bn-orig": [], "en-orig": [], "hi-orig": [], "en": [], "ar": []}


def test_dubbed_video_uses_the_track_of_its_own_language_not_the_first_listed():
    # A video with dubbed audio has one -orig track per dub; ar-orig is listed first.
    assert find_orig_caption_code({"automatic_captions": DUBBED, "language": "en-US"}) == "en-orig"


def test_language_comes_from_the_original_audio_track_when_the_video_does_not_say():
    info = {"automatic_captions": DUBBED, "formats": [
        {"acodec": "mp4a", "language": "ar", "format_note": "Arabic, medium"},
        {"acodec": "mp4a", "language": "hi", "format_note": "Hindi original (default), medium"}]}
    assert find_orig_caption_code(info) == "hi-orig"


def test_single_orig_track_and_no_track():
    assert find_orig_caption_code({"automatic_captions": {"hi-orig": [], "en": []}, "language": "en"}) == "hi-orig"
    assert find_orig_caption_code({"automatic_captions": {"en": []}}) is None
    assert find_orig_caption_code({}) is None
