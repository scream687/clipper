from app.model import Word
from app.quotes import cluster, index_words, locate


def _words(text: str):
    return [Word(id=i, text=w, display=w, start=float(i), end=i + 1.0) for i, w in enumerate(text.split())]


def test_common_phrases_said_many_times_locate_nothing():
    words = _words(" ".join(["you know what i mean"] * 5))
    assert locate("you know what i mean you know what i mean", index_words(words)) is None


def test_a_phrase_repeated_inside_the_text_counts_once():
    words = _words("x " * 50 + "send money to india with zero fees today" + " y" * 50)
    ad = "send money to india with zero fees today " * 4
    assert len(cluster(ad, index_words(words), min_hits=2)) == 5  # 5 distinct 4-word runs, not 20


def test_devanagari_text_and_roman_display_both_match():
    words = [Word(id=i, text=t, display=d, start=float(i), end=i + 1.0)
             for i, (t, d) in enumerate(zip("यह बहुत बड़ी बात है भाई".split(), "yeh bahut badi baat hai bhai".split()))]
    index = index_words(words)
    assert locate("bahut badi baat hai bhai", index) == (1.0, 2.0)
    assert locate("बहुत बड़ी बात है भाई", index) == (1.0, 2.0)
