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


def test_only_a_signed_in_youtube_session_is_copied_from_the_browser():
    import http.cookiejar
    from app.youtube import signed_in_jar

    def cookie(domain, name):
        return http.cookiejar.Cookie(0, name, "v", None, False, domain, True, domain.startswith("."), "/", False,
                                     True, None, False, None, None, {})
    jar = [cookie(".youtube.com", "LOGIN_INFO"), cookie(".google.com", "SAPISID"), cookie(".bank.com", "session"),
           cookie("accounts.google.com", "x")]
    assert sorted(c.domain for c in signed_in_jar(jar)) == [".google.com", ".youtube.com", "accounts.google.com"]
    assert signed_in_jar([cookie(".youtube.com", "VISITOR_INFO1_LIVE")]) == []  # signed out: no use


def test_a_bot_check_fetches_the_session_once_and_retries(monkeypatch):
    import pytest
    from app import youtube
    calls, refreshed = [], []
    monkeypatch.setattr(youtube, "refresh_cookies", lambda: refreshed.append(1) or "chrome")

    @youtube.signed_in
    def fetch():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("ERROR: [youtube] x: Sign in to confirm you're not a bot.")
        return "ok"
    assert fetch() == "ok" and len(calls) == 2 and refreshed == [1]

    monkeypatch.setattr(youtube, "refresh_cookies", lambda: None)  # no signed-in browser: a clear message
    @youtube.signed_in
    def blocked():
        raise RuntimeError("Sign in to confirm you're not a bot")
    with pytest.raises(RuntimeError, match="Open youtube.com"):
        blocked()
