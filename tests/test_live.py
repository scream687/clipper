from app import library, pipeline
import pytest

from app.youtube import live_manifest, segments, window_playlists


def _playlist(first, n, name):
    return (f"#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXT-X-MEDIA-SEQUENCE:{first}\n#EXT-X-DISCONTINUITY-SEQUENCE:2\n"
            + "".join(f"#EXT-X-PROGRAM-DATE-TIME:x\n#EXTINF:2.0,\nhttps://h/{name}/sq/{k}/seg\n" for k in range(first, first + n)))


def test_segments_are_numbered_from_the_media_sequence():
    seg = segments(_playlist(100, 3, "v"))
    assert seg == {100: (2.0, "https://h/v/sq/100/seg"), 101: (2.0, "https://h/v/sq/101/seg"), 102: (2.0, "https://h/v/sq/102/seg")}


def test_video_and_audio_start_on_the_same_segment_even_when_one_list_is_newer():
    video, audio = segments(_playlist(100, 50, "v")), segments(_playlist(101, 50, "a"))  # audio fetched a segment later
    (v, a), covered = window_playlists([video, audio], 20)
    assert covered == 20 and "#EXT-X-ENDLIST" in v
    first = lambda text: [line for line in text.splitlines() if line.startswith("https")][0]
    last = lambda text: [line for line in text.splitlines() if line.startswith("https")][-1]
    assert first(v).replace("/v/", "/") == first(a).replace("/a/", "/") == "https://h/sq/140/seg"
    assert last(v).endswith("/sq/149/seg") and last(a).endswith("/sq/149/seg")  # the newest segment both have


def test_a_window_longer_than_the_rewind_buffer_takes_what_there_is():
    (v,), covered = window_playlists([segments(_playlist(0, 15, "v"))], 1800)
    assert covered == 30 and v.count("#EXTINF") == 15
    with pytest.raises(RuntimeError):
        window_playlists([segments(_playlist(0, 5, "v")), segments(_playlist(50, 5, "a"))], 10)


def test_the_best_muxed_live_format_up_to_1080p_is_picked():
    formats = [{"protocol": "m3u8_native", "url": "a", "vcodec": "avc1", "acodec": "mp4a", "height": 720, "tbr": 2500},
               {"protocol": "m3u8_native", "url": "b", "vcodec": "avc1", "acodec": "mp4a", "height": 1080, "tbr": 5000},
               {"protocol": "m3u8_native", "url": "c", "vcodec": "vp9", "acodec": "mp4a", "height": 1440, "tbr": 9000},
               {"protocol": "https", "url": "d", "vcodec": "avc1", "acodec": "none", "height": 1080}]
    assert live_manifest({"formats": formats}) == ("b", None)
    split = [{"protocol": "m3u8_native", "url": "v", "vcodec": "avc1", "acodec": "none", "height": 1080, "format_id": "270"},
             {"protocol": "m3u8_native", "url": "a1", "vcodec": "none", "format_id": "233"},
             {"protocol": "m3u8_native", "url": "a2", "vcodec": "none", "format_id": "234"}]
    assert live_manifest({"formats": split}) == ("v", "a2")


def test_a_live_link_is_captured_now_instead_of_waiting_for_the_stream_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "VIDEOS", tmp_path / "videos")
    monkeypatch.setattr(pipeline, "fetch_info", lambda url: {"title": "Stream", "channel": "C", "is_live": True, "formats": []})
    captured = []

    def capture(info, out, window_s, log):
        captured.append(window_s)
        raise RuntimeError("stop here")  # the rest of analysis is covered elsewhere
    monkeypatch.setattr(pipeline, "download_live_window", capture)
    monkeypatch.setattr(pipeline, "download_source", lambda *a: (_ for _ in ()).throw(AssertionError("must not wait for the end")))
    video = pipeline.create_from_youtube("https://youtu.be/live1", {"live_s": 600}, "9:16")
    try:
        pipeline.analyse(video.id, log=lambda m: None)
    except RuntimeError:
        pass
    stored = library.load_video(video.id)
    assert captured == [600] and stored.options["live"] and "(live, last 10 min" in stored.title


def test_a_watched_live_link_clips_only_chat_booms_and_is_transcribed_as_hindi(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "VIDEOS", tmp_path / "videos")
    monkeypatch.setattr(pipeline, "fetch_info", lambda url: {"title": "Stream", "channel": "C", "is_live": True, "formats": []})
    monkeypatch.setattr(pipeline, "download_live_window", lambda *a: (_ for _ in ()).throw(AssertionError("no backlog capture")))
    video = pipeline.create_from_youtube("https://youtu.be/live2", {"live_s": 600, "watch_chat": True}, "9:16")
    done = pipeline.analyse(video.id, log=lambda m: None)
    assert done.stage == "ready" and done.language == "hi" and library.load_clips(video.id) == []
