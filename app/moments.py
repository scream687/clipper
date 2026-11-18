"""Find clip-worthy moments in a transcript and turn them into clips."""

from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from app.ai import AIError, AIUnavailable
from app.model import Clip, Video, Word
from app.comments import comment_note, mentions_in
from app.quotes import STOP, tokens
from app.retention import pick_peaks, score_range
from app.sentences import split_sentences

AskFn = Callable[[str, dict, str], dict]
MAX_CLIPS = 30
PARALLEL_PARTS = 4  # AI calls at once; each part is an independent read
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


def build_prompt(video: Video, sentences: list[list[Word]], min_s: int, max_s: int, max_clips: int, direction: str,
                 trends: str = "", taste: str = "", first_id: int = 0, part: str = "") -> str:
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
    if trends:
        replay += ("\nWhat India is searching and watching right now:\n" + trends + "\n"
                   "When a strong moment genuinely touches one of these (a person, event, game, match, company or "
                   "topic), score it higher and use the trending name in its title, hook and hashtags so it gets "
                   "found. Never force a trend onto a moment that is not about it.\n")
    if taste:
        replay += ("\nThe user rated clips from earlier videos. Pick moments like the ones they rated high and "
                   "unlike the ones they rated low:\n" + taste + "\n")
    steer = f"\nUSER DIRECTION (highest priority, follow it): {direction.strip()}\n" if direction.strip() else ""
    loud = set(video.loud)
    is_loud = lambda s: any(t in loud for t in range(int(s[0].start), int(s[-1].end) + 1))
    lines = [f"[s{i} {_clock(s[0].start)}]{' (LOUD)' if is_loud(s) else ''} {' '.join(w.display for w in s)}"
             for i, s in enumerate(sentences, start=first_id)]
    if loud:
        replay += ("\n(LOUD) marks sentences where the audio got much louder than usual: laughing, shouting, "
                   "big reactions. Treat them as hints of strong moments, not proof.\n")
    return (
        f"Video: {video.title}\nChannel: {video.channel}\n{replay}{steer}\n"
        f"{part}Pick up to {max_clips} standalone moments that would work as vertical short clips, best first. "
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


QUESTION_MAX_S = 12   # a question longer than this is a monologue, not a lead-in
QUESTION_GAP_S = 3    # ...and it must be asked right before the answer starts
QUESTION_MIN_WORDS = 3  # 'theek hai?', 'right?' are tags, not questions


def lead_with_question(video: Video, clip: Clip, sentences: list[list[Word]], prev_last: int | None, max_s: float) -> Clip:
    """A clip that opens on an answer starts on the question asked just before it: in a podcast the
    question is the hook. Only when it is short, adjacent, fits the length limit and is in no other clip."""
    at = next((i for i, s in enumerate(sentences) if s[0].id <= clip.first_word <= s[-1].id), None)
    if not at or sentences[at][0].id != clip.first_word:
        return clip
    question = sentences[at - 1]
    words = video.words
    if (not question[-1].display.rstrip().endswith("?") or len(question) < QUESTION_MIN_WORDS
            or question[-1].end - question[0].start > QUESTION_MAX_S):
        return clip
    if words[clip.first_word].start - question[-1].end > QUESTION_GAP_S:
        return clip
    if words[clip.last_word].end - question[0].start > max_s * (1 + EDGE_SLACK):
        return clip
    if prev_last is not None and prev_last >= question[0].id:
        return clip
    return clip.model_copy(update={"first_word": question[0].id})


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
    sentences = split_sentences(video.words)
    ends = sorted(c.last_word for c in refined)
    refined = [lead_with_question(video, c, sentences, next((e for e in reversed(ends) if e < c.first_word), None), max_s)
               for c in refined]
    spans = [(video.words[c.first_word].start, video.words[c.last_word].end) for c in refined]
    return [c.model_copy(update={"retention": score_range(video.heatmap, a, b),
                                 "mentions": mentions_in(video.comments, a, b) if video.comments else None})
            for c, (a, b) in zip(refined, spans)]


PART_S = 30 * 60      # long videos are read in parts this long: one pass over 2+ hours loses the middle
PART_OVERLAP_S = 90   # parts overlap, so a moment on a boundary is whole in one of them
RANK_WORDS = 70       # words of each candidate shown to the ranking pass

RANK_SCHEMA = {
    "type": "object",
    "properties": {"scores": {"type": "array", "items": {
        "type": "object", "properties": {"id": {"type": "integer"}, "score": {"type": "integer", "minimum": 1, "maximum": 10}},
        "required": ["id", "score"], "additionalProperties": False}}},
    "required": ["scores"], "additionalProperties": False,
}


def split_parts(sentences: list[list[Word]], part_s: float = PART_S, overlap_s: float = PART_OVERLAP_S) -> list[tuple[int, int]]:
    """[(first sentence id, last sentence id)] windows of about `part_s` seconds, overlapping by `overlap_s`."""
    if not sentences:
        return []
    parts, first = [], 0
    while True:
        limit = sentences[first][0].start + part_s
        last = first
        while last + 1 < len(sentences) and sentences[last + 1][0].start < limit:
            last += 1
        parts.append((first, last))
        if last + 1 >= len(sentences):
            return parts
        back = last + 1
        while back > first + 1 and sentences[last][-1].end - sentences[back - 1][0].start < overlap_s:
            back -= 1
        first = back


def build_rank_prompt(video: Video, sentences: list[list[Word]], moments: list[dict], direction: str) -> str:
    blocks = []
    for n, m in enumerate(moments):
        words = [w.display for s in sentences[m["first_sentence"]:m["last_sentence"] + 1] for w in s]
        text = " ".join(words[:RANK_WORDS]) + ("…" if len(words) > RANK_WORDS else "")
        start, end = sentences[m["first_sentence"]][0].start, sentences[m["last_sentence"]][-1].end
        signals = [f"{mentions_in(video.comments, start, end)} comments point here"] if video.comments else []
        if video.heatmap:
            signals.append(f"replay {score_range(video.heatmap, start, end):.1f}x")
        blocks.append(f"[{n}] {_clock(start)} \"{m['title']}\" hook: {m['hook']} | {', '.join(signals)}\n{text}")
    steer = f"\nUSER DIRECTION (highest priority): {direction.strip()}\n" if direction.strip() else ""
    return (f"Video: {video.title}\n{steer}\nThese candidate Shorts were picked from different parts of the video. "
            "Score every one 1-10 against the others, as the editor deciding which to post first: hook strength in "
            "the first 3 seconds, a complete payoff, emotion or surprise, how well it stands alone, and how much viewers "
            "reacted (comments, replays). Use the whole scale; at most a quarter of them may get 9 or 10.\n\n"
            + "\n\n".join(blocks))


def rank_moments(video: Video, sentences: list[list[Word]], moments: list[dict], ask: AskFn, direction: str) -> list[dict]:
    """One pass that scores candidates from all parts against each other. Best effort: keeps scores on failure."""
    if len(moments) < 2:
        return moments
    try:
        answer = ask(build_rank_prompt(video, sentences, moments, direction), RANK_SCHEMA, SYSTEM)
    except (AIError, AIUnavailable):
        return moments
    scores = {item["id"]: item["score"] for item in answer.get("scores", []) if isinstance(item, dict)}
    return [{**m, "score": scores.get(n, m["score"])} for n, m in enumerate(moments)]


COLD_OPEN_MAX_S = 300   # a podcast's teaser montage plays within its first five minutes
REPEAT_AFTER_S = 120    # a teaser line is said again at least this much later in the episode
COLD_OPEN_SHARE = 0.7   # ...and most lines of the opening are such lines
COLD_OPEN_BREAK = 3     # this many unrepeated lines in a row end the montage
COLD_OPEN_MIN_LINES = 4


def cold_open_end(sentences: list[list[Word]]) -> int:
    """Id of the last sentence of a cold-open teaser (a montage of lines from later in the episode), else -1.

    Clips cut from the teaser are fragments of moments that play whole later, so it is not offered to the AI."""
    later: dict[tuple[str, ...], float] = {}
    stream = [(t, w.start) for s in sentences for w in s for t in tokens(w.display)]
    for i in range(len(stream) - 3):
        later[tuple(t for t, _ in stream[i:i + 4])] = stream[i][1]  # the last time each 4-word run is said

    def repeated(s: list[Word]) -> bool:
        toks = tokens(" ".join(w.display for w in s))
        return any(later.get(tuple(toks[k:k + 4]), 0) > s[-1].end + REPEAT_AFTER_S
                   for k in range(len(toks) - 3) if not all(t in STOP for t in toks[k:k + 4]))

    end, hits, misses_in_row = -1, 0, 0
    for i, s in enumerate(sentences):
        if s[0].start > COLD_OPEN_MAX_S or misses_in_row >= COLD_OPEN_BREAK:
            break
        if repeated(s):
            hits, misses_in_row = hits + 1, 0
            if hits / (i + 1) >= COLD_OPEN_SHARE:
                end = i
        else:
            misses_in_row += 1
    return end if end + 1 >= COLD_OPEN_MIN_LINES else -1


def find_clips(video: Video, ask: AskFn, min_s: int = DEFAULT_MIN_S, max_s: int = DEFAULT_MAX_S,
               max_clips: int = MAX_CLIPS, direction: str = "", trends: str = "", taste: str = "") -> list[Clip]:
    sentences = split_sentences(video.words)
    if not sentences:
        raise RuntimeError("The transcript is empty, so there is nothing to clip.")
    skip = cold_open_end(sentences) + 1
    if skip >= len(sentences):
        skip = 0
    parts = [(skip + a, skip + b) for a, b in split_parts(sentences[skip:], PART_S, PART_OVERLAP_S)]
    per_part = max_clips if len(parts) == 1 else min(max_clips, max(4, -(-max_clips // len(parts)) + 2))

    def pick(n_part: tuple[int, tuple[int, int]]) -> list[dict]:
        n, (first, last) = n_part
        part = (f"This is part {n + 1} of {len(parts)} of the transcript ({_clock(sentences[first][0].start)}-"
                f"{_clock(sentences[last][-1].end)}); pick only from this part.\n" if len(parts) > 1 else "")
        prompt = build_prompt(video, sentences[first:last + 1], min_s, max_s, per_part, direction, trends, taste,
                              first_id=first, part=part)
        try:
            found = ask(prompt, SCHEMA, SYSTEM).get("moments", [])
        except AIError:
            return []  # one bad part must not lose the others
        return [m for m in found if first <= m.get("first_sentence", -1) <= m.get("last_sentence", -1) <= last]

    with ThreadPoolExecutor(max_workers=PARALLEL_PARTS) as pool:
        moments = [m for batch in pool.map(pick, enumerate(parts)) for m in batch]
    if len(parts) > 1:
        moments = rank_moments(video, sentences, moments, ask, direction)
    clips = moments_to_clips(video, sentences, moments, min_s, max_s, max_clips, video.shape)
    if not clips:
        raise RuntimeError("The AI did not return any usable moments for this video.")
    return refine_edges(video, clips, ask, min_s, max_s)
