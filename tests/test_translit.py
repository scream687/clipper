import pytest

from app import translit


@pytest.fixture(autouse=True)
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(translit, "CACHE", tmp_path / "translit.json")


def test_devanagari_words_are_romanized_once_and_cached():
    calls = []

    def ask(prompt, schema, system):
        calls.append(prompt)
        lines = [l for l in prompt.splitlines() if "\t" in l]
        fake = {"नमस्ते": "namaste", "प्रॉपर्टी": "property"}
        return {"items": [{"i": int(l.split("\t")[0]), "r": fake[l.split("\t")[1]]} for l in lines]}

    first = translit.romanize(["नमस्ते", "property", "प्रॉपर्टी", "नमस्ते"], ask)
    again = translit.romanize(["नमस्ते"], ask)

    assert first == {"नमस्ते": "namaste", "प्रॉपर्टी": "property"}
    assert again == {"नमस्ते": "namaste"} and len(calls) == 1


def test_words_the_ai_skips_or_leaves_in_devanagari_fall_back_to_rules():
    ask = lambda prompt, schema, system: {"items": [{"i": 0, "r": "दोस्त"}, {"i": 99, "r": "junk"}]}
    out = translit.romanize(["दोस्त", "नमस्ते"], ask)
    assert all(not translit.needs_roman(v) and v for v in out.values())


def test_ai_failure_still_gives_roman_text():
    def ask(prompt, schema, system):
        raise RuntimeError("limit reached")
    out = translit.romanize(["नमस्ते"], ask)
    assert out["नमस्ते"] and not translit.needs_roman(out["नमस्ते"])


def test_sentences_are_converted_in_context_and_word_counts_are_kept():
    def ask(prompt, schema, system):
        return {"sentences": [{"s": 0, "words": ["I", "think", "aap"]}, {"s": 2, "words": ["only one"]}]}
    out = translit.romanize_in_context([["आई", "थिंक", "आप"], ["already", "english"], ["वो", "आई"]],
                                       lambda p, s, y: ask(p, s, y) if "s0" in p else {"items": []})
    assert out[0] == ["I", "think", "aap"]
    assert out[1] == ["already", "english"]
    assert len(out[2]) == 2 and not any(translit.needs_roman(w) for w in out[2])  # wrong count -> word-by-word fallback
