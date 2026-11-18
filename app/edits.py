"""The editor's second pass over each clip, in one AI call per batch of clips: which words to emphasise, which
line is the punchline (a meme sound), and where a B-roll insert or a number/name card would help."""

from typing import Callable

from app.ai import AIError, AIUnavailable
from app.model import Clip, Video

AskFn = Callable[[str, dict, str], dict]
BATCH = 8
MAX_EMPHASIS = 6
MAX_INSERTS = 3

SCHEMA = {
    "type": "object",
    "properties": {"clips": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "clip": {"type": "integer"},
            "emphasis": {"type": "array", "items": {"type": "integer"}, "maxItems": MAX_EMPHASIS},
            "punchline": {"type": "integer"},
            "inserts": {"type": "array", "maxItems": MAX_INSERTS, "items": {
                "type": "object",
                "properties": {"first_word": {"type": "integer"}, "last_word": {"type": "integer"},
                               "kind": {"type": "string", "enum": ["stock", "card"]},
                               "text": {"type": "string"}, "sub": {"type": "string"}, "query": {"type": "string"}},
                "required": ["first_word", "last_word", "kind", "text", "sub", "query"], "additionalProperties": False}},
        },
        "required": ["clip", "emphasis", "punchline", "inserts"], "additionalProperties": False}}},
    "required": ["clips"], "additionalProperties": False,
}

SYSTEM = ("You are the editor who finishes Shorts and Reels for a top Indian creator. You return only the requested JSON.")


def build_prompt(video: Video, clips: list[Clip]) -> str:
    by_id = {w.id: w for w in video.words}
    parts = []
    for n, clip in enumerate(clips):
        cut = set(clip.cut_word_ids)
        words = " ".join(f"{i}:{by_id[i].display}" for i in range(clip.first_word, clip.last_word + 1) if i in by_id and i not in cut)
        parts.append(f"Clip {n} ({clip.title}): {words}")
    return (f"Video: {video.title} ({video.content or 'unknown kind'})\nFor each clip, word ids are given as id:word.\n"
            "- emphasis: 2 to 6 word ids that carry the point (numbers, names, the surprising word). They pop in the "
            "captions in colour, so never pick filler words.\n"
            "- punchline: the id of the word that lands the joke or the shock, where a meme sound effect would get a "
            "laugh; -1 if the clip has no such moment (most serious clips do not).\n"
            "- inserts: 0 to 3 moments to cut away from the speaker for 1.5 to 3 seconds, each a word id range:\n"
            "  kind 'card' when a number, a statistic or a name is said: text is that number or name, very short "
            "(e.g. '90%', '₹50 lakh', 'Elon Musk'), sub is a 2-6 word line explaining it;\n"
            "  kind 'stock' when a concrete thing is described that stock footage can show: query is a 2-4 word English "
            "stock video search (e.g. 'hospital corridor', 'stock market crash'), text is 1-3 key words to show.\n"
            "  Never in the first 2 seconds, never over the speaker's emotional peak, at least 6 seconds apart. "
            "Gameplay and shows rarely need inserts.\n\n" + "\n".join(parts))


def _inside(clip: Clip, ids: list[int]) -> list[int]:
    cut = set(clip.cut_word_ids)
    return [i for i in ids if clip.first_word <= i <= clip.last_word and i not in cut]


def apply(clips: list[Clip], answer: dict) -> list[Clip]:
    """Clips with the AI's picks, every word id checked against the clip."""
    picks = {item["clip"]: item for item in answer.get("clips", []) if isinstance(item.get("clip"), int)}
    result = []
    for n, clip in enumerate(clips):
        item = picks.get(n)
        if not item:
            result.append(clip)
            continue
        inserts = []
        for ins in item.get("inserts", [])[:MAX_INSERTS]:
            a, b = ins.get("first_word", -1), ins.get("last_word", -1)
            if clip.first_word < a <= b <= clip.last_word and ins.get("text", "").strip() and ins.get("kind") in ("stock", "card"):
                inserts.append({"first_word": a, "last_word": b, "kind": ins["kind"], "text": ins["text"].strip()[:40],
                                "sub": ins.get("sub", "").strip()[:60], "query": ins.get("query", "").strip()[:60]})
        meme = item.get("punchline", -1)
        result.append(clip.model_copy(update={
            "emphasis_words": _inside(clip, item.get("emphasis", []))[:MAX_EMPHASIS],
            "meme_word": meme if _inside(clip, [meme]) else None,
            "broll": inserts,
        }))
    return result


def decorate(video: Video, clips: list[Clip], ask: AskFn, log: Callable[[str], None] = lambda m: None) -> list[Clip]:
    """Add the editor's picks to clips; on any AI trouble the clips come back unchanged (they still work)."""
    out = []
    for start in range(0, len(clips), BATCH):
        batch = clips[start:start + BATCH]
        log(f"Planning edits {min(start + BATCH, len(clips))}/{len(clips)}")
        try:
            out += apply(batch, ask(build_prompt(video, batch), SCHEMA, SYSTEM))
        except (AIUnavailable, AIError, KeyError, TypeError) as error:
            log(f"Could not plan edits for these clips ({str(error)[:120]}); they keep plain captions")
            out += batch
    return out
