"""Group words into sentences. Auto-captions often have no punctuation, so pauses and length also end a sentence.

YouTube word timings are back to back (a word ends where the next starts), so a pause hides inside a word
that lasts much longer than its letters need. `pause_after` counts that slack as pause.
"""

from app.model import Word

PAUSE_S = 0.9
MAX_WORDS = 30
CHAR_S = 0.07      # rough speaking time per letter
WORD_BASE_S = 0.1  # ...plus this per word
ENDINGS = (".", "?", "!", "।", "…")


def pause_after(word: Word, nxt: Word | None) -> float:
    if nxt is None:
        return float("inf")
    slack = (word.end - word.start) - WORD_BASE_S - CHAR_S * len(word.text)
    return max(0.0, nxt.start - word.end) + max(0.0, slack)


def split_sentences(words: list[Word]) -> list[list[Word]]:
    sentences: list[list[Word]] = []
    current: list[Word] = []
    for index, word in enumerate(words):
        current.append(word)
        nxt = words[index + 1] if index + 1 < len(words) else None
        if word.text.endswith(ENDINGS) or pause_after(word, nxt) > PAUSE_S:
            sentences.append(current)
            current = []
        elif len(current) >= MAX_WORDS:
            # No clear end in 30 words: break after the biggest pause in the second half, not mid-phrase.
            half = MAX_WORDS // 2
            cut = max(range(half, len(current)), key=lambda i: pause_after(current[i], words[index - len(current) + i + 1]))
            sentences.append(current[:cut + 1])
            current = current[cut + 1:]
    if current:
        sentences.append(current)
    return sentences
