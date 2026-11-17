"""Devanagari -> Roman Hinglish, the way people type it ("kharidne", "property").

Each distinct word is converted once by the AI in batches and cached in
data/translit.json, so a 3-hour video costs a handful of calls and a re-run costs
none. Words the AI skips fall back to a rule-based converter.
"""

import json
import os
import re
import unicodedata
from typing import Callable

from app import DATA_DIR

CACHE = DATA_DIR / "translit.json"
BATCH = 300
DEVANAGARI = re.compile(r"[ऀ-ॿ]")
AskFn = Callable[[str, dict, str], dict]

SYSTEM = "You convert Hindi written in Devanagari into Roman-letter Hinglish. You return only the requested JSON."
SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {
        "type": "object", "properties": {"i": {"type": "integer"}, "r": {"type": "string"}},
        "required": ["i", "r"], "additionalProperties": False}}},
    "required": ["items"], "additionalProperties": False,
}


def needs_roman(text: str) -> bool:
    return bool(DEVANAGARI.search(text))


def fallback(word: str) -> str:
    """Rule-based last resort: ITRANS, lower-cased, with anything non-ASCII dropped."""
    from indic_transliteration import sanscript
    from indic_transliteration.sanscript import transliterate

    text = transliterate(word, sanscript.DEVANAGARI, sanscript.ITRANS).replace(".N", "n").replace("M", "n")
    text = unicodedata.normalize("NFKD", text).lower()
    return re.sub(r"[^a-z0-9'.,!?-]", "", text) or word


def _load_cache() -> dict[str, str]:
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_cache(cache: dict[str, str]) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, CACHE)


def _prompt(batch: list[str]) -> str:
    lines = "\n".join(f"{i}\t{w}" for i, w in enumerate(batch))
    return ("Convert every Hindi word below from Devanagari to Roman letters, spelled the way Indians type Hinglish "
            "on WhatsApp and Instagram (e.g. नमस्ते -> namaste, खरीदने -> kharidne, बहुत -> bahut, क्यों -> kyun).\n"
            "English words written in Devanagari must come back in normal English spelling "
            "(प्रॉपर्टी -> property, हेल्थ -> health, जेनरेशन -> generation).\n"
            "Keep any punctuation attached exactly where it is. Use lowercase unless it is a name.\n"
            "Return one item per input line with the same number i.\n\n" + lines)


def romanize(texts: list[str], ask: AskFn, log: Callable[[str], None] = lambda m: None) -> dict[str, str]:
    """Map every distinct Devanagari text in `texts` to Roman Hinglish."""
    cache = _load_cache()
    todo = sorted({t for t in texts if needs_roman(t) and t not in cache})
    for start in range(0, len(todo), BATCH):
        batch = todo[start:start + BATCH]
        log(f"Writing Hinglish captions {min(start + BATCH, len(todo))}/{len(todo)} words")
        try:
            answer = ask(_prompt(batch), SCHEMA, SYSTEM)
        except Exception as error:  # the AI being unavailable must not lose the captions: fall back per word
            log(f"AI conversion failed ({str(error)[:120]}); using the rule-based spelling for this batch")
            answer = {"items": []}
        got = {item["i"]: item["r"].strip() for item in answer.get("items", [])
               if isinstance(item.get("i"), int) and 0 <= item["i"] < len(batch) and item.get("r", "").strip()}
        for i, word in enumerate(batch):
            roman = got.get(i, "")
            cache[word] = roman if roman and not needs_roman(roman) else fallback(word)
        _save_cache(cache)
    return {t: cache[t] for t in texts if t in cache}
