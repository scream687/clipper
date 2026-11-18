"""Pacing, the way an editor tightens a talking clip: fillers and stutters cut, long pauses shortened, and a
punch-in zoom on alternate shots so the picture changes every few seconds."""

import re

from app.model import Clip, Video

FILLERS = {"um", "umm", "ummm", "uh", "uhh", "uhm", "erm", "er", "hmm", "hmmm", "mm", "mmm", "ah", "ahh",
           "अं", "उम्म", "हम्म", "अम्म", "आं"}
# Words a speaker stutters on ("I I think", "the the"). Hindi doubling ("jaldi jaldi", "kya kya") is meaning, never cut.
STUTTER = {"i", "the", "a", "an", "and", "so", "we", "you", "it", "is", "to", "like", "but", "that", "this", "my",
           "toh", "to", "ki", "ke", "main", "mai", "hum", "aur"}
_word = lambda text: re.sub(r"[^\wऀ-ॿ]", "", text.lower())


def filler_cuts(video: Video, clip: Clip) -> list[int]:
    """Word ids inside the clip that are fillers, or the first of a stuttered pair. Never the clip's edge words."""
    by_id = {w.id: w for w in video.words}
    ids = [i for i in range(clip.first_word + 1, clip.last_word) if i in by_id]
    cuts = []
    for i in ids:
        text = _word(by_id[i].text) or _word(by_id[i].display)
        nxt = by_id.get(i + 1)
        if text in FILLERS or _word(by_id[i].display) in FILLERS:
            cuts.append(i)
        elif nxt and text in STUTTER and text == (_word(nxt.text) or _word(nxt.display)) and nxt.start - by_id[i].end < 0.5:
            cuts.append(i)
    return cuts


def tighten(video: Video, clip: Clip) -> Clip:
    """The clip with its fillers cut (added to any cuts already there), if its pacing is on."""
    if not clip.tighten:
        return clip
    if video.content in ("gameplay", "stream"):  # the pauses are the gameplay the clip is about: keep them whole
        return clip.model_copy(update={"tighten": False})
    cuts = sorted(set(clip.cut_word_ids) | set(filler_cuts(video, clip)))
    span = clip.last_word - clip.first_word + 1
    return clip if len(cuts) >= span else clip.model_copy(update={"cut_word_ids": cuts})


MIN_SHOT_S = 2.5   # a punch-in or punch-out happens at most this often...
MAX_SHOT_S = 4.5
FORCE_FLIP_S = 5.5   # no beat for this long: flip without one   # ...and at least this often when the speaker gives a place to do it (a caption start)


def zoom_spans(cut_times: list[float], beat_times: list[float], duration: float) -> list[tuple[float, float]]:
    """Output-time spans to show punched in. Every jump cut flips in/out (that hides the cut); between cuts the
    shot flips at a caption start once it has run MAX_SHOT_S. Pure, so it is unit-tested."""
    flips, last = [], 0.0
    cuts = set(round(t, 3) for t in cut_times)
    for t in sorted(set(round(t, 3) for t in cut_times + beat_times)) + [duration]:
        while t - last > FORCE_FLIP_S and last + MAX_SHOT_S < duration - 1.0:  # a long stretch with no beat (laughter,
            last = round(last + MAX_SHOT_S, 3)                                # music): flip anyway so it never sits still
            flips.append(last)
        if t <= 0 or t >= duration - 1.0:
            continue
        since = t - last
        if (t in cuts and since >= 1.0) or since >= MAX_SHOT_S or (since >= MIN_SHOT_S and t in cuts):
            flips.append(t)
            last = t
    spans = []
    for n in range(0, len(flips), 2):
        end = flips[n + 1] if n + 1 < len(flips) else duration
        spans.append((flips[n], end))
    return spans
