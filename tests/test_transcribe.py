import json

from app.transcribe import words_from_whisper_json


def token(text_bytes: bytes, start_ms: int, end_ms: int, dtw_cs: int = -1):
    # whisper.cpp writes raw token bytes, which can split one Devanagari letter across tokens
    return {"text": text_bytes.decode("utf-8", errors="surrogateescape"), "offsets": {"from": start_ms, "to": end_ms}, "t_dtw": dtw_cs}


def write(tmp_path, tokens):
    raw = json.dumps({"transcription": [{"offsets": {"from": 0, "to": 9000}, "tokens": tokens}]}, ensure_ascii=False)
    path = tmp_path / "out.json"
    path.write_bytes(raw.encode("utf-8", errors="surrogateescape"))
    return path


def test_split_devanagari_bytes_are_rejoined_into_whole_words(tmp_path):
    word = " नमस्ते".encode()  # leading space starts a new word
    tokens = [token(b"[_BEG_]", 0, 0), token(word[:5], 0, 200, 2), token(word[5:], 200, 400),
              token(" दोस्तों".encode(), 500, 900, 51)]
    words = words_from_whisper_json(write(tmp_path, tokens))
    assert [w["word"] for w in words] == ["नमस्ते", "दोस्तों"]
    assert words[0]["start"] == 0.02 and words[1]["start"] == 0.51  # DTW times win when present


def test_punctuation_sticks_to_its_word_and_special_tokens_are_skipped(tmp_path):
    tokens = [token(b" hello", 0, 300), token(b",", 300, 320), token(b"[_TT_50]", 320, 320), token(b" world", 400, 800)]
    words = words_from_whisper_json(write(tmp_path, tokens))
    assert [w["word"] for w in words] == ["hello,", "world"]
    assert words[0]["end"] <= words[1]["start"]


def test_long_pauses_do_not_stretch_a_word(tmp_path):
    tokens = [token(b" one", 0, 300), token(b" two", 5000, 5300)]
    words = words_from_whisper_json(write(tmp_path, tokens))
    assert words[0]["end"] - words[0]["start"] <= 1.2


def test_whisper_loops_over_noise_are_dropped_but_real_repeats_stay():
    from app.transcribe import invented
    assert invented("ek min dekho".split() * 13, 16.4)          # a loop over game sound
    assert invented(["jhaal", "jhaal"], 30.0)                    # two words across 30 s of music
    assert not invented("no bro no bro no bro".split(), 2.0)     # a streamer really saying it
    assert not invented("L bhai GTA L bhai".split(), 2.5)
    assert not invented("aaj hum baat karenge health ke baare mein".split(), 4.0)
