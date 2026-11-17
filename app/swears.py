"""Swear words for the per-clip filter: English and common Hindi/Hinglish (Roman and Devanagari)."""

import re

_ROOTS = [
    # English
    "fuck", "shit", "bitch", "bastard", "asshole", "dick", "cunt", "motherfuck", "bullshit", "pussy", "whore", "slut",
    # Hindi / Hinglish, Roman spellings
    "chutiya", "chutia", "chootiya", "madarchod", "maderchod", "behenchod", "bhenchod", "bhenchod", "bc", "mc", "bsdk",
    "bhosdike", "bhosdi", "bhosda", "gandu", "gaandu", "lund", "lauda", "lavda", "randi", "harami", "haramkhor",
    "kutta", "kamina", "kamine", "jhaant", "tatti", "saala", "saali",
    # Devanagari
    "चूतिया", "चुतिया", "मादरचोद", "बहनचोद", "भेनचोद", "भोसड़ी", "भोसडी", "गांडू", "लंड", "लौड़ा", "रंडी", "हरामी", "कमीना", "साला",
]
_EXACT_ONLY = {"bc", "mc", "dick", "saala", "saali", "kutta", "साला"}  # too short or common to match as prefixes
_CLEAN = re.compile(r"[^\w]+", re.UNICODE)


def is_swear(word: str) -> bool:
    token = _CLEAN.sub("", word.lower())
    if not token:
        return False
    for root in _ROOTS:
        if token == root or (root not in _EXACT_ONLY and token.startswith(root)):
            return True
    return False


def star(word: str) -> str:
    """Keep the first letter, star the rest: fucking -> f******."""
    return word[0] + "*" * (len(word) - 1) if len(word) > 1 else "*"
