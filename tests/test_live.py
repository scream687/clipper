from app import library, pipeline
from app.youtube import live_manifest, target_duration


def test_segment_length_is_read_from_the_playlist():
    assert target_duration("#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:2\n#EXTINF:2.0,\nseg1.ts\n") == 2.0
    assert target_duration("#EXTM3U\n") == 5.0


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
