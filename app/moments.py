"""Find clip-worthy moments in a transcript and turn them into clips."""

from typing import Callable

from app.model import Clip, Video, Word
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
            "title": {"type": "string"}, "hook": {"type": "string"}, "description": {"type": "string"},
            "score": {"type": "integer", "minimum": 1, "maximum": 10},
        },
        "required": ["first_sentence", "last_sentence", "title", "hook", "description", "score"],
        "additionalProperties": False}}},
    "required": ["moments"], "additionalProperties": False,
}


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
    steer = f"\nUSER DIRECTION (highest priority, follow it): {direction.strip()}\n" if direction.strip() else ""
    lines = [f"[s{i} {_clock(s[0].start)}] {' '.join(w.display for w in s)}" for i, s in enumerate(sentences)]
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
        "Hindi or Hinglish, write in Roman letters (Hinglish as people text it). score is 1-10, spread honestly.\n\n"
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
                          retention=score_range(video.heatmap, start, end)))
        if len(clips) >= max_clips:
            break
    return clips


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
    return clips
