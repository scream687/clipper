"""The render plan: everything the preview and the export need, in OUTPUT time.

`build_plan` is the single place where clip edits (trim, cut words, fixed text,
crop keys, look, shape) become concrete frames, timings and caption lines.
The browser preview and the ffmpeg renderer both consume its output, so the two
cannot disagree.
"""

from functools import lru_cache
from pathlib import Path

from PIL import ImageFont
from pydantic import BaseModel

from app.model import Clip, CropKey, Video
from app.swears import is_swear, star

FONT_DIR = Path("/System/Library/Fonts/Supplemental")
FONTS = {"Arial Black": FONT_DIR / "Arial Black.ttf", "Arial Bold": FONT_DIR / "Arial Bold.ttf"}
SIZES = {"9:16": (1080, 1920), "1:1": (1080, 1080), "16:9": (1920, 1080)}
CAPTION_Y = {"9:16": 0.68, "1:1": 0.78, "16:9": 0.84}  # caption block centre, fraction of height
MARGIN_X = 80              # px kept clear at each side, at 1080 short side
GROUP_PAUSE_S = 0.6        # a pause longer than this starts a new caption group
GROUP_STICK_S = 0.3        # gaps shorter than this keep the previous group on screen
HOOK_SECONDS = 4.0
BEEP_PAD_S = 0.08          # Whisper word edges are loose; widen beeps a little

# Shared by the ASS writer and the CSS preview. Sizes are px at a 1080 short side.
LOOKS = {
    "highlight": {"font": "Arial Black", "size": 76, "uppercase": True, "max_lines": 1, "max_words": 3,
                  "color": "#FFFFFF", "active": "#FFE14D", "outline": 7, "box": False},
    "karaoke":   {"font": "Arial Black", "size": 66, "uppercase": True, "max_lines": 2, "max_words": 8,
                  "color": "#FFFFFF", "active": "#FFE14D", "outline": 6, "box": False},
    "pop":       {"font": "Arial Black", "size": 108, "uppercase": True, "max_lines": 1, "max_words": 1,
                  "color": "#FFFFFF", "active": "#FFE14D", "outline": 8, "box": False},
    "boxed":     {"font": "Arial Bold", "size": 54, "uppercase": False, "max_lines": 2, "max_words": 14,
                  "color": "#FFFFFF", "active": "#FFFFFF", "outline": 0, "box": True},
}
HOOK_STYLE = {"font": "Arial Black", "size": 64, "min_size": 40, "y": 0.15, "max_lines": 3}
HANDLE_STYLE = {"font": "Arial Bold", "size": 34, "y": 0.91}
CREDIT_STYLE = {"font": "Arial Bold", "size": 26, "y": 0.965}


class Segment(BaseModel):
    start_frame: int  # source frame numbers; [start, end)
    end_frame: int


class PlanWord(BaseModel):
    word_id: int
    text: str
    start: float  # output seconds
    end: float


class Line(BaseModel):
    word_ids: list[int]
    width_px: float


class CaptionGroup(BaseModel):
    start: float
    end: float
    lines: list[Line]


class TextBlock(BaseModel):
    lines: list[str]
    start: float
    end: float


class Plan(BaseModel):
    fps: float
    width: int
    height: int
    duration: float
    segments: list[Segment]
    words: list[PlanWord]
    captions: list[CaptionGroup]
    look: str
    style: dict
    caption_y: float
    safe_width_px: float
    framing: str
    source_width: int
    source_height: int
    crop_w: int
    crop_h: int
    crop_keys: list[CropKey]  # output time
    hook: TextBlock | None
    handle: str
    credit: str
    beeps: list[tuple[float, float]]

    def crop_at(self, t: float) -> float:
        return interpolate(self.crop_keys, t)


def interpolate(keys: list[CropKey], t: float) -> float:
    """Crop centre (0..1) at time t. Glides between keys, holds before a jump key. web/preview.js mirrors this."""
    if not keys:
        return 0.5
    if t <= keys[0].t:
        return keys[0].cx
    for before, after in zip(keys, keys[1:]):
        if t < after.t:
            if after.jump or after.t == before.t:
                return before.cx
            return before.cx + (after.cx - before.cx) * (t - before.t) / (after.t - before.t)
    return keys[-1].cx


@lru_cache(maxsize=64)
def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS[name]), size)


def measure(text: str, font: str, size: int) -> float:
    return _font(font, size).getlength(text)


def _scale(width: int, height: int) -> float:
    return min(width, height) / 1080


def _segments(video: Video, clip: Clip, by_id: dict) -> list[tuple[list[int], float, float]]:
    """Runs of kept words as (word_ids, src_start, src_end). A cut removes its words and the pause after them."""
    cuts = set(clip.cut_word_ids)
    ids = [i for i in range(clip.first_word, clip.last_word + 1) if i in by_id]
    runs: list[list[int]] = []
    for wid in ids:
        if wid in cuts:
            if runs and runs[-1]:
                runs.append([])
            continue
        if not runs:
            runs.append([])
        runs[-1].append(wid)
    runs = [r for r in runs if r]
    out = []
    for index, run in enumerate(runs):
        if index == len(runs) - 1:
            end = by_id[run[-1]].end
        else:  # a run followed by a cut ends where the first cut word starts
            end = by_id[next(i for i in range(run[-1] + 1, runs[index + 1][0]) if i in by_id)].start
        out.append((run, by_id[run[0]].start, end))
    return out


def _effective_keys(clip: Clip) -> list[CropKey]:
    """Manual keys win from the first one onwards; each manual key is a hard jump."""
    auto = sorted(clip.auto_keys, key=lambda k: k.t)
    manual = sorted(clip.manual_keys, key=lambda k: k.t)
    if not manual:
        return auto
    first_manual = manual[0].t
    return [k for k in auto if k.t < first_manual] + [k.model_copy(update={"jump": True}) for k in manual]


def _caption_groups(words: list[PlanWord], seg_of: dict[int, int], look: dict, safe_px: float, scale: float) -> list[CaptionGroup]:
    size = round(look["size"] * scale)
    groups: list[list[PlanWord]] = []
    for word in words:
        current = groups[-1] if groups else None
        if (current is None or len(current) >= look["max_words"]
                or seg_of[word.word_id] != seg_of[current[-1].word_id]
                or word.start - current[-1].end > GROUP_PAUSE_S):
            groups.append([word])
        else:
            groups.append(groups.pop() + [word])

    result: list[CaptionGroup] = []
    pending: list[list[PlanWord]] = []
    for group in groups:  # split groups whose words need more lines than the look allows
        lines: list[list[PlanWord]] = [[]]
        for word in group:
            candidate = lines[-1] + [word]
            text = " ".join(w.text.upper() if look["uppercase"] else w.text for w in candidate)
            if lines[-1] and measure(text, look["font"], size) > safe_px:
                lines.append([word])
            else:
                lines[-1] = candidate
        for start in range(0, len(lines), look["max_lines"]):
            pending.append(lines[start:start + look["max_lines"]])

    for index, block in enumerate(pending):
        flat = [w for line in block for w in line]
        end = flat[-1].end
        if index + 1 < len(pending):
            next_start = pending[index + 1][0][0].start
            if 0 <= next_start - end < GROUP_STICK_S:
                end = next_start
        result.append(CaptionGroup(
            start=flat[0].start, end=end,
            lines=[Line(word_ids=[w.word_id for w in line],
                        width_px=measure(" ".join(w.text.upper() if look["uppercase"] else w.text for w in line), look["font"], size))
                   for line in block]))
    return result


def _wrap(text: str, font: str, size: int, max_px: float) -> list[str]:
    lines: list[str] = [""]
    for token in text.split():
        candidate = f"{lines[-1]} {token}".strip()
        if lines[-1] and measure(candidate, font, size) > max_px:
            lines.append(token)
        else:
            lines[-1] = candidate
    return lines


def _fit_text(text: str, font: str, size: int, min_size: int, max_px: float, max_lines: int) -> tuple[list[str], int]:
    """Shrink until the whole text fits in max_lines; never drop words."""
    for candidate in range(size, min_size - 1, -2):
        lines = _wrap(text, font, candidate, max_px)
        if len(lines) <= max_lines:
            return lines, candidate
    return _wrap(text, font, min_size, max_px), min_size


def build_plan(video: Video, clip: Clip, handle: str = "") -> Plan:
    fps = video.fps
    by_id = {w.id: w for w in video.words}
    width, height = SIZES[clip.shape]
    scale = _scale(width, height)
    safe_px = width - 2 * MARGIN_X * scale
    look = LOOKS[clip.look]

    runs = _segments(video, clip, by_id)
    segments: list[Segment] = []
    offsets: list[float] = []
    cursor = 0
    for _, start, end in runs:
        first, last = round(start * fps), round(end * fps)
        last = max(last, first + 1)
        segments.append(Segment(start_frame=first, end_frame=last))
        offsets.append(cursor / fps)
        cursor += last - first
    duration = cursor / fps

    def to_out(index: int, t_src: float) -> float:
        seg = segments[index]
        t = min(max(t_src, seg.start_frame / fps), seg.end_frame / fps)
        return offsets[index] + t - seg.start_frame / fps

    plan_words: list[PlanWord] = []
    seg_of: dict[int, int] = {}
    beeps: list[tuple[float, float]] = []
    for index, (run, _, _) in enumerate(runs):
        for wid in run:
            word = by_id[wid]
            text = clip.text_fixes.get(wid, word.display)
            start, end = to_out(index, word.start), to_out(index, word.end)
            if clip.swear_filter and (is_swear(word.text) or is_swear(text)):
                text = star(text)
                beeps.append((max(0.0, start - BEEP_PAD_S), min(duration, end + BEEP_PAD_S)))
            plan_words.append(PlanWord(word_id=wid, text=text, start=start, end=end))
            seg_of[wid] = index

    keys = _effective_keys(clip)
    out_keys: list[CropKey] = []
    if keys:
        for index, seg in enumerate(segments):
            seg_start, seg_end = seg.start_frame / fps, seg.end_frame / fps
            out_keys.append(CropKey(t=offsets[index], cx=interpolate(keys, seg_start), jump=index > 0))
            for key in keys:
                if seg_start < key.t < seg_end:
                    out_keys.append(key.model_copy(update={"t": to_out(index, key.t)}))

    if clip.framing == "fill" or clip.shape == "16:9" and video.width / video.height >= 16 / 9 - 0.01:
        crop_w, crop_h = video.width, video.height
    else:
        aspect = width / height
        crop_w = min(video.width, round(video.height * aspect))
        crop_h = min(video.height, round(crop_w / aspect))
    crop_w, crop_h = crop_w // 2 * 2, crop_h // 2 * 2

    hook, hook_size = None, round(HOOK_STYLE["size"] * scale)
    if clip.show_hook and clip.hook.strip():
        lines, hook_size = _fit_text(clip.hook.strip(), HOOK_STYLE["font"], hook_size,
                                     round(HOOK_STYLE["min_size"] * scale), safe_px, HOOK_STYLE["max_lines"])
        hook = TextBlock(lines=lines, start=0.0, end=min(HOOK_SECONDS, duration))

    return Plan(
        fps=fps, width=width, height=height, duration=duration, segments=segments, words=plan_words,
        captions=_caption_groups(plan_words, seg_of, look, safe_px, scale), look=clip.look,
        style={**look, "size": round(look["size"] * scale), "outline": round(look["outline"] * scale),
               "hook": {**HOOK_STYLE, "size": hook_size},
               "handle": {**HANDLE_STYLE, "size": round(HANDLE_STYLE["size"] * scale)},
               "credit": {**CREDIT_STYLE, "size": round(CREDIT_STYLE["size"] * scale)}},
        caption_y=CAPTION_Y[clip.shape], safe_width_px=safe_px, framing=clip.framing,
        source_width=video.width, source_height=video.height, crop_w=crop_w, crop_h=crop_h,
        crop_keys=out_keys, hook=hook,
        handle=handle.strip() if clip.show_handle else "",
        credit=f"Credit: {video.channel}" if clip.show_credit and video.channel else "",
        beeps=beeps,
    )
