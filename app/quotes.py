"""Find where a piece of text (a comment quoting a line, a Short's captions) was said in the transcript."""

import re
from collections import defaultdict
from statistics import median

from app.model import Word

N = 4              # a hit is 4 words in a row, the same as in the transcript
MAX_SPOTS = 2      # a 4-word run said more than twice ("you know what i") locates nothing
CLUSTER_S = 60     # hits further than this from the median belong to another place
TOKEN = re.compile(r"[a-z0-9ऀ-ॿ]+")
STOP = {"the", "a", "an", "i", "you", "is", "it", "to", "and", "of", "that", "in", "this", "so", "he", "she", "we",
        "they", "was", "be", "are", "for", "on", "with", "like", "just", "what", "do", "me", "my", "your", "hai",
        "ki", "ke", "ka", "ko", "to", "toh", "aur", "ye", "yeh", "wo", "woh", "na", "bhi", "se", "mein", "main"}

Index = dict[tuple[str, ...], list[float]]


def tokens(text: str) -> list[str]:
    return TOKEN.findall(text.lower())


def index_words(words: list[Word]) -> Index:
    """4-word runs -> start times. Both the transcribed text and the Roman display are indexed, so a
    comment typed in either script can match. ponytail: exact runs only; Hinglish spelled differently
    ('kya' vs 'kia') misses, add a spelling normaliser if quote hits on Hindi videos stay low."""
    index: Index = defaultdict(list)
    for field in ("text", "display"):
        stream = [(t, w.start) for w in words for t in tokens(getattr(w, field))]
        for i in range(len(stream) - N + 1):
            gram = tuple(t for t, _ in stream[i:i + N])
            if field == "display" and stream[i][1] in index.get(gram, []):
                continue  # same run already indexed from the text
            index[gram].append(stream[i][1])
    return index


def hits(text: str, index: Index) -> list[float]:
    """Times of the distinctive 4-word runs in `text` that also occur in the transcript."""
    toks = tokens(text)
    found = []
    for i in range(len(toks) - N + 1):
        gram = tuple(toks[i:i + N])
        spots = index.get(gram)
        if spots and len(spots) <= MAX_SPOTS and not all(t in STOP for t in gram):
            found.append(spots[0])
    return found


def cluster(text: str, index: Index, min_hits: int = 2, reach_s: float = CLUSTER_S) -> list[float]:
    """Distinct hit times around the place `text` quotes most; empty when it does not clearly quote one place.
    Distinct times, so a phrase repeated in `text` (a sponsor read said three times) counts once."""
    found = sorted(set(hits(text, index)))
    if len(found) < min_hits:
        return []
    centre = median(found)
    near = [t for t in found if abs(t - centre) <= reach_s]
    return near if len(near) >= min_hits else []


def locate(text: str, index: Index, min_hits: int = 2) -> tuple[float, float] | None:
    """(first, last) time of the place `text` quotes, or None when it does not clearly quote one place."""
    near = cluster(text, index, min_hits)
    return (near[0], near[-1]) if near else None
