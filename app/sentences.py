"""Group words into sentences. Auto-captions often have no punctuation, so pauses and length also end a sentence."""

from app.model import Word

PAUSE_S = 0.7
MAX_WORDS = 30
ENDINGS = (".", "?", "!", "।", "…")


def split_sentences(words: list[Word]) -> list[list[Word]]:
    sentences: list[list[Word]] = []
    current: list[Word] = []
    for index, word in enumerate(words):
        current.append(word)
        nxt = words[index + 1] if index + 1 < len(words) else None
        ends = (word.text.endswith(ENDINGS) or len(current) >= MAX_WORDS
                or nxt is None or nxt.start - word.end > PAUSE_S)
        if ends:
            sentences.append(current)
            current = []
    return sentences
