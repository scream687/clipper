"""Find clip-worthy moments in a transcript and turn them into clips."""

from typing import Callable

from app.ai import AIError, AIUnavailable
from app.model import Clip, Video, Word
from app.comments import comment_note, mentions_in
from app.retention import pick_peaks, score_range
from app.sentences import split_sentences

AskFn = Callable[[str, dict, str], dict]
MAX_CLIPS = 30
DEFAULT_MIN_S, DEFAULT_MAX_S = 20, 60

SYSTEM = ("You are a senior short-form video editor who cuts viral Shorts, Reels and TikToks from long podcasts "
          "and talks. You return only the requested JSON.")

SCHEMA = {
    "type": "object",
    "properties": {"moments": {"type": "array", "maxItems": MAX_CLIPS, "items": {
        "type": "object",
        "properties": {
            "first_sentence": {"type": "integer"}, "last_sentence": {"type": "integer"},
            "title": {"type": "string"}, "hook": {"type": "string"}, "description": {"type": "string"}, "hashtags": {"type": "string"},
            "score": {"type": "integer", "minimum": 1, "maximum": 10},
        },
        "required": ["first_sentence", "last_sentence", "title", "hook", "description", "hashtags", "score"],
        "additionalProperties": False}}},
    "required": ["moments"], "additionalProperties": False,
}


MAX_TAGS = 8


def clean_hashtags(text: str) -> str:
    """'#Health, longevity #health' -> '#health #longevity': letters, digits and _ only, no repeats."""
    tags = []
    for token in text.replace(",", " ").split():
        tag = "".join(ch for ch in token.lstrip("#") if ch.isalnum() or ch == "_").lower()
        if tag and f"#{tag}" not in tags:
            tags.append(f"#{tag}")
    return " ".join(tags[:MAX_TAGS])


def _clock(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def build_prompt(video: Video, sentences: list[list[Word]], min_s: int, max_s: int, max_clips: int, direction: str) -> str:
    peaks = pick_peaks(video.heatmap)
    replay = ""
    if peaks:
        replay = ("\nViewers replay these moments most (YouTube 'Most replayed'): "
                  + ", ".join(f"{_clock(p['time'])} ({p['value']:.2f})" for p in peaks)
                  + ". Prefer segments containing them when the content is strong.\n")
    comments = comment_note(video.comments)
    if comments:
        replay += ("\nWhat viewers said (treat timestamp mentions as strong evidence of a great moment, and use liked "
                   "comments to judge which topics and lines will travel):\n" + comments + "\n")
    steer = f"\nUSER DIRECTION (highest priority, follow it): {direction.strip()}\n" if direction.strip() else ""
    loud = set(video.loud)
    is_loud = lambda s: any(t in loud for t in range(int(s[0].start), int(s[-1].end) + 1))
    lines = [f"[s{i} {_clock(s[0].start)}]{' (LOUD)' if is_loud(s) else ''} {' '.join(w.display for w in s)}"
             for i, s in enumerate(sentences)]
    if loud:
        replay += ("\n(LOUD) marks sentences where the audio got much louder than usual: laughing, shouting, "
                   "big reactions. Treat them as hints of strong moments, not proof.\n")
    return (
        f"Video: {video.title}\nChannel: {video.channel}\n{replay}{steer}\n"
        f"Pick up to {max_clips} standalone moments that would work as vertical short clips, best first. "
        "Fewer is fine when the video is short or weak: only include moments you would actually post.\n"
        "A good moment has at least one of: a strong hook in its first 3 seconds (a surprising line, question or "
        "bold claim), a complete useful insight or piece of advice, something funny or emotional, or a bold or "
        "controversial opinion. It must make sense without the rest of the video.\n"
        f"Each moment should run {min_s}-{max_s} seconds; use the sentence timestamps to judge length. "
        "Never pick overlapping moments or near-duplicates.\n"
        "Return sentence ids: first_sentence and last_sentence are the numbers after 's'.\n"
        "Write title (max 60 characters), hook (max 12 words, shown on screen for the first seconds) and "
        "description (max 150 characters) in the language and script the transcript uses; if it is "
        "Hindi or Hinglish, write in Roman letters (Hinglish as people text it). hashtags: 3-6 hashtags for the post, "
        "space separated, mixing the specific topic, the speaker or show, and one or two broad discovery tags; "
        "Roman letters only. score is 1-10, spread honestly.\n\n"
        "Transcript:\n" + "\n".join(lines)
    )


def _fit(first: int, last: int, sentences: list[list[Word]], min_s: float, max_s: float) -> tuple[int, int]:
    """Grow a too-short range by whole sentences (after, then before); shrink a too-long one from the end."""
    def length(a, b):
        return sentences[b][-1].end - sentences[a][0].start

    while length(first, last) < min_s:
        if last + 1 < len(sentences) and length(first, last + 1) <= max_s:
            last += 1
        elif first > 0 and length(first - 1, last) <= max_s:
            first -= 1
        else:
            break
    while last > first and length(first, last) > max_s:
        last -= 1
    return first, last


def moments_to_clips(video: Video, sentences: list[list[Word]], moments: list[dict], min_s: float, max_s: float,
                     max_clips: int, shape: str) -> list[Clip]:
    clips: list[Clip] = []
    taken: list[tuple[float, float]] = []
    for moment in sorted(moments, key=lambda m: -m["score"]):
        first, last = moment["first_sentence"], moment["last_sentence"]
        if not (0 <= first <= last < len(sentences)):
            continue
        first, last = _fit(first, last, sentences, min_s, max_s)
        start, end = sentences[first][0].start, sentences[last][-1].end
        if any(start < b and end > a for a, b in taken):
            continue
        taken.append((start, end))
        clips.append(Clip(index=len(clips), first_word=sentences[first][0].id, last_word=sentences[last][-1].id,
                          shape=shape, title=moment["title"].strip()[:80], hook=moment["hook"].strip(),
                          description=moment["description"].strip(), score=moment["score"],
                          hashtags=clean_hashtags(moment.get("hashtags", "")),
                          retention=score_range(video.heatmap, start, end),
                          mentions=mentions_in(video.comments, start, end) if video.comments else None))
        if len(clips) >= max_clips:
            break
    return clips


EDGE_BEFORE, EDGE_AFTER = 30, 20  # words shown around each edge: outside then inside the clip
EDGE_SLACK = 0.15                  # refined length may stray this share outside min/max

EDGE_SCHEMA = {
    "type": "object",
    "properties": {"clips": {"type": "array", "items": {
        "type": "object",
        "properties": {"clip": {"type": "integer"}, "first_word": {"type": "integer"}, "last_word": {"type": "integer"}},
        "required": ["clip", "first_word", "last_word"], "additionalProperties": False}}},
    "required": ["clips"], "additionalProperties": False,
}


def _edge_windows(video: Video, clip: Clip) -> tuple[range, range]:
    n = len(video.words)
    return (range(max(0, clip.first_word - EDGE_BEFORE), min(clip.last_word, clip.first_word + EDGE_AFTER) + 1),
            range(max(clip.first_word, clip.last_word - EDGE_AFTER), min(n, clip.last_word + EDGE_BEFORE + 1)))


def build_edge_prompt(video: Video, clips: list[Clip], min_s: int, max_s: int) -> str:
    words = video.words

    def show(ids: range) -> str:
        return f"(from {_clock(words[ids.start].start)}) " + " ".join(f"{i}:{words[i].display}" for i in ids)

    blocks = []
    for n, clip in enumerate(clips):
        head, tail = _edge_windows(video, clip)
        blocks.append(f"Clip {n}: {clip.title}\nNow starts at word {clip.first_word}, ends at word {clip.last_word} "
                      f"({words[clip.last_word].end - words[clip.first_word].start:.0f} s).\n"
                      f"START window: {show(head)}\nEND window: {show(tail)}")
    return (
        "These clips were cut from an auto-captioned transcript, so their edges can land mid-phrase. "
        "Each word is shown as id:word. For every clip choose first_word from its START window and last_word "
        "from its END window.\n"
        "first_word: the clip must open on its hook, the first word of a sentence or question that grabs attention "
        "at once and makes sense with no earlier context. If the moment answers a question asked just before, "
        "start at that question. Never start mid-phrase; skip throwaway openers (so, and, to, okay, hum, like, "
        "you know, toh, aur).\n"
        "last_word: end right after the point or punchline lands, on the last word of a complete sentence. "
        "Never end mid-phrase or on a dangling word (and, but, the, of, ki, ke, ka, to, aur); "
        "leave out the start of the next topic.\n"
        f"Keep each clip about {min_s}-{max_s} seconds long. Keep the current edge when it is already right.\n\n"
        + "\n\n".join(blocks)
    )


def apply_edges(video: Video, clips: list[Clip], answer: dict, min_s: float, max_s: float) -> list[Clip]:
    """Move clip edges to the refined words when they stay in their windows, length limits and do not overlap."""
    words = video.words
    proposed = {item.get("clip"): item for item in answer.get("clips", []) if isinstance(item, dict)}
    result = list(clips)
    for n, clip in enumerate(clips):
        item = proposed.get(n)
        if not item:
            continue
        head, tail = _edge_windows(video, clip)
        first, last = item["first_word"], item["last_word"]
        if first not in head or last not in tail or last <= first:
            continue
        length = words[last].end - words[first].start
        if not (min_s * (1 - EDGE_SLACK) <= length <= max_s * (1 + EDGE_SLACK)):
            continue
        others = [c for k, c in enumerate(result) if k != n]
        if any(first <= o.last_word and last >= o.first_word for o in others):
            continue
        result[n] = clip.model_copy(update={"first_word": first, "last_word": last})
    return result


# A Hindi clause ends on its auxiliary; a clip that stops one word before it sounds cut off.
CLAUSE_ENDINGS = {"hai", "hain", "hu", "hoon", "hun", "tha", "thi", "the", "ho", "hoga", "hogi", "honge", "gaya", "gayi", "na"}


def land_ending(video: Video, clip: Clip, next_first: int | None) -> Clip:
    """Pull in up to two trailing auxiliaries ('kar sakte' -> 'kar sakte hain'), never into the next clip."""
    last = clip.last_word
    for _ in range(2):
        nxt = last + 1
        if nxt >= len(video.words) or (next_first is not None and nxt >= next_first):
            break
        if video.words[nxt].display.lower().strip(".,?!।") not in CLAUSE_ENDINGS:
            break
        last = nxt
    return clip if last == clip.last_word else clip.model_copy(update={"last_word": last})


def refine_edges(video: Video, clips: list[Clip], ask: AskFn, min_s: int, max_s: int) -> list[Clip]:
    """Second pass: snap each clip to a clean opening and a landed ending. Best effort; keeps edges on failure."""
    if not clips:
        return clips
    try:
        answer = ask(build_edge_prompt(video, clips, min_s, max_s), EDGE_SCHEMA, SYSTEM)
    except (AIError, AIUnavailable):
        answer = {}
    refined = apply_edges(video, clips, answer, min_s, max_s)
    starts = sorted(c.first_word for c in refined)
    refined = [land_ending(video, c, next((f for f in starts if f > c.last_word), None)) for c in refined]
    spans = [(video.words[c.first_word].start, video.words[c.last_word].end) for c in refined]
    return [c.model_copy(update={"retention": score_range(video.heatmap, a, b),
                                 "mentions": mentions_in(video.comments, a, b) if video.comments else None})
            for c, (a, b) in zip(refined, spans)]


def find_clips(video: Video, ask: AskFn, min_s: int = DEFAULT_MIN_S, max_s: int = DEFAULT_MAX_S,
               max_clips: int = MAX_CLIPS, direction: str = "") -> list[Clip]:
    sentences = split_sentences(video.words)
    if not sentences:
        raise RuntimeError("The transcript is empty, so there is nothing to clip.")
    prompt = build_prompt(video, sentences, min_s, max_s, max_clips, direction)
    answer = ask(prompt, SCHEMA, SYSTEM)
    clips = moments_to_clips(video, sentences, answer.get("moments", []), min_s, max_s, max_clips, video.shape)
    if not clips:
        raise RuntimeError("The AI did not return any usable moments for this video.")
    return refine_edges(video, clips, ask, min_s, max_s)
