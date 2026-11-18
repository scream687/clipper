"""The render plan: everything the preview and the export need, in OUTPUT time.

`build_plan` is the single place where clip edits (trim, cut words, fixed text,
crop keys, look, shape) become concrete frames, timings and caption lines.
The browser preview and the ffmpeg renderer both consume its output, so the two
cannot disagree.
"""

from functools import lru_cache
from statistics import median
from pathlib import Path

from PIL import ImageFont
from pydantic import BaseModel

from app import assets
from app.model import Clip, CropKey, Video
from app import sfx as sfx_library
from app.pacing import zoom_spans
from app.swears import is_swear, star

FONT_DIR = Path("/System/Library/Fonts/Supplemental")
FONTS = {"Arial Black": FONT_DIR / "Arial Black.ttf", "Arial Bold": FONT_DIR / "Arial Bold.ttf", "Arial": FONT_DIR / "Arial.ttf"}
SIZES = {"9:16": (1080, 1920), "4:5": (1080, 1350), "1:1": (1080, 1080), "16:9": (1920, 1080)}
CAPTION_Y = {"9:16": 0.68, "4:5": 0.76, "1:1": 0.78, "16:9": 0.84}  # caption block centre, fraction of height
CAPTION_GAP = 40           # px between the bottom of a whole-frame picture and the captions, at 1080
CAPTION_Y_MAX = 0.86       # ...but never lower than this: the handle and the app's buttons live below
# Instagram Reels / Shorts safe zone at 1080x1920 (from the video-edit skill): nothing below y 1470, and the right
# 100 px column (like, comment, share) clear from y 1155 down. Captions keep 110 px each side, so they stay centred.
CAPTION_Y_MAX_VERTICAL = 0.74
MARGIN_X = 80              # px kept clear at each side, at 1080 short side
MARGIN_X_VERTICAL = 110
GROUP_PAUSE_S = 0.6        # a pause longer than this starts a new caption group
GROUP_STICK_S = 0.3        # gaps shorter than this keep the previous group on screen
HOOK_SECONDS = 4.0
FOLLOW_SECONDS = 3.0       # the follow badge closes the clip
LOGO_MARGIN = 40           # px from the edges, at 1080
EMOJI_PX = 150             # emoji picture size at a 1080 short side
EMOJI_HOLD_S = 1.6         # how long an emoji stays up (less if the next one arrives)
BEEP_PAD_S = 0.08          # Whisper word edges are loose; widen beeps a little
GAP_MAX_S = 0.35           # pacing: a pause longer than this between words is cut down...
GAP_KEEP_S = 0.06          # ...to this much breath on each side of the cut
PUNCH_ZOOM = 1.15          # punch-in scale on alternate shots
ACCENT = "#FFE14D"         # key words and the progress bar
ZOOM_ANCHOR_Y = 0.4        # punch-ins zoom towards this height of the frame (faces sit high), shared with the preview

# Caption looks. Sizes are em sizes in px at a 1080 short side
# (CSS font-size), used by the captions layer in web/overlay/.
LOOKS = {
    "highlight": {"font": "Arial Black", "size": 60, "uppercase": True, "max_lines": 1, "max_words": 3,
                  "color": "#FFFFFF", "active": "#FFE14D", "outline": 7, "box": False},
    "karaoke":   {"font": "Arial Black", "size": 54, "uppercase": True, "max_lines": 2, "max_words": 8,
                  "color": "#FFFFFF", "active": "#FFE14D", "outline": 6, "box": False},
    "pop":       {"font": "Arial Black", "size": 84, "uppercase": True, "max_lines": 1, "max_words": 1,
                  "color": "#FFFFFF", "active": "#FFE14D", "outline": 8, "box": False},
    "build":     {"font": "Arial Black", "size": 58, "uppercase": True, "max_lines": 2, "max_words": 6,
                  "color": "#FFFFFF", "active": "#FFFFFF", "outline": 7, "box": False},
    "line":      {"font": "Arial Black", "size": 50, "uppercase": False, "max_lines": 2, "max_words": 10,
                  "color": "#FFFFFF", "active": "#FFFFFF", "outline": 6, "box": False},
    "boxed":     {"font": "Arial Bold", "size": 46, "uppercase": False, "max_lines": 2, "max_words": 14,
                  "color": "#FFFFFF", "active": "#FFFFFF", "outline": 0, "box": True},
}
HOOK_STYLE = {"font": "Arial Black", "size": 46, "min_size": 30, "y": 0.15, "max_lines": 3}
HANDLE_STYLE = {"font": "Arial Bold", "size": 30, "y": 0.91}
CREDIT_STYLE = {"font": "Arial Bold", "size": 22, "y": 0.965}
SAFE_SMALL_Y = {"handle": 0.745, "credit": 0.762}  # 9:16: just under the captions, above the platform's caption row
FOLLOW_STYLE = {"font": "Arial Black", "size": 44, "y": 0.15, "color": "#111111", "box": "#FFE14D"}


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


class EmojiEvent(BaseModel):
    emoji: str
    start: float
    end: float


class TextBlock(BaseModel):
    lines: list[str]
    start: float
    end: float


class Insert(BaseModel):
    start: float   # output seconds
    end: float
    kind: str      # stock | card
    text: str
    sub: str = ""
    query: str = ""
    src_t: float   # source second it covers (the card's background still comes from here)


INSERT_MIN_S, INSERT_MAX_S = 1.5, 3.5
INSERT_NOT_BEFORE_S = 1.5   # the opening belongs to the speaker and the hook


def _inserts(clip: Clip, words: list[PlanWord], by_id: dict, duration: float) -> list[Insert]:
    """B-roll and cards in output time: on kept words, 1.5-3.5 s long, never in the opening, never overlapping."""
    out: list[Insert] = []
    for b in clip.broll:
        span = [w for w in words if b["first_word"] <= w.word_id <= b["last_word"]]
        if not span:
            continue
        start = span[0].start
        end = min(max(span[-1].end, start + INSERT_MIN_S), start + INSERT_MAX_S, duration - 0.3)
        if start < INSERT_NOT_BEFORE_S or end - start < 1.0 or (out and start < out[-1].end + 0.5):
            continue
        out.append(Insert(start=round(start, 3), end=round(end, 3), kind=b["kind"], text=b["text"], sub=b.get("sub", ""),
                          query=b.get("query", ""), src_t=by_id[span[0].word_id].start))
    return out


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
    fit_spans: list[tuple[float, float]] = []
    canvas: dict = {}
    split: dict = {}
    music: dict | None = None   # file, volume and peak (0..1 gain), riser_s
    logo: dict | None = None    # x, y, w, h in output px
    follow: TextBlock | None = None
    tweet: dict | None = None   # X post header: name, handle, lines, theme and its layout in output px (app/template.py)   # framing "split": top [x, y, w, h] facecam crop and crop_w, crop_h of the video below (source px), bottom_cx (0..1), top_h (output px)  # whole-frame picture: background, colours, card rectangle (output px), corner radius  # output time: whole frame on a blurred fill inside a cropped clip
    hook: TextBlock | None
    handle: str
    credit: str
    beeps: list[tuple[float, float]]
    emojis: list[EmojiEvent] = []
    emoji_px: int = EMOJI_PX
    emoji_y: float = 0.0  # centre of the emoji, px from the top
    zooms: list[tuple[float, float]] = []  # output-time spans shown punched in (crop framing only)
    zoom: float = PUNCH_ZOOM
    zoom_anchor_y: float = ZOOM_ANCHOR_Y
    sfx: list[dict] = []  # sound effects: t (output s), file (data/sfx/), gain
    emphasis: list[int] = []  # word ids shown in the accent colour with a bounce
    accent: str = ACCENT
    progress: bool = False
    inserts: list[Insert] = []  # full-frame cutaways (Remotion), under the captions

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


def key_at(keys: list[CropKey], t: float) -> CropKey | None:
    """The key in force at time t: the last one at or before t, else the first."""
    if not keys:
        return None
    return next((k for k in reversed(keys) if k.t <= t), keys[0])


def _fit_spans(keys: list[CropKey], duration: float) -> list[tuple[float, float]]:
    """Output-time stretches where a fit key is in force (whole frame on a blurred fill)."""
    spans = []
    for key, after in zip(keys, [*keys[1:], None]):
        end = after.t if after else duration
        if key.fit and end > key.t:
            if spans and abs(spans[-1][1] - key.t) < 1e-9:
                spans[-1] = (spans[-1][0], end)
            else:
                spans.append((key.t, end))
    return spans


@lru_cache(maxsize=64)
def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS[name]), size)


def measure(text: str, font: str, size: int) -> float:
    return _font(font, size).getlength(text)


@lru_cache(maxsize=8)
def _line_ratio(font: str) -> float:
    """(ascent + descent) / em, measured at a large size so integer rounding does not distort it."""
    ascent, descent = _font(font, 4096).getmetrics()
    return (ascent + descent) / 4096


def line_height(font: str, size: float) -> float:
    """Ascent + descent in px: libass reads an ASS Fontsize as this, and the preview uses it as line height."""
    return round(size * _line_ratio(font), 2)


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
            if clip.tighten:  # ...but not after a long pause: that pause goes too
                end = min(end, by_id[run[-1]].end + GAP_KEEP_S)
        out.append((run, by_id[run[0]].start, end))
    return _close_gaps(out, by_id) if clip.tighten else out


def _close_gaps(runs: list[tuple[list[int], float, float]], by_id: dict) -> list[tuple[list[int], float, float]]:
    """Split runs at pauses longer than GAP_MAX_S, keeping GAP_KEEP_S of breath each side: a jump cut."""
    out = []
    for run, start, end in runs:
        part = [run[0]]
        for prev, wid in zip(run, run[1:]):
            if by_id[wid].start - by_id[prev].end > GAP_MAX_S:
                out.append((part, start, by_id[prev].end + GAP_KEEP_S))
                part, start = [wid], by_id[wid].start - GAP_KEEP_S
            else:
                part.append(wid)
        out.append((part, start, end))
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


CAM_TOP = (0.3, 0.5)  # share of the output height the facecam may take; the video gets the rest
SPLIT_HOOK_Y = 0.03   # split: hook top and follow badge centre sit above the streamer's head, not over their face
SPLIT_FOLLOW_Y = 0.06


def _even(n: float) -> int:
    return int(n) // 2 * 2


def _split(clip: Clip, video: Video, width: int, height: int) -> dict:
    """Facecam on top, sized to the cam's own shape; the video below, cropped to fill. No facecam found:
    the face the auto framing followed takes the top half."""
    sw, sh = video.width, video.height
    box = clip.split_cam or clip.cam_box
    if box:
        bx, by, bw, bh = box[0] * sw, box[1] * sh, box[2] * sw, box[3] * sh
        top_h = _even(min(max(width * bh / bw, CAM_TOP[0] * height), CAM_TOP[1] * height))
    else:
        top_h = _even(height / 2)
        cx = median([k.cx for k in clip.auto_keys if not k.fit] or [0.5])
        bh = sh; bw = min(sw, sh * width / top_h); bx, by = cx * sw - bw / 2, 0
    aspect = width / top_h  # cut the box to the top's shape around its centre
    cw, ch = (bh * aspect, bh) if bw / bh > aspect else (bw, bw / aspect)
    cw, ch = _even(min(cw, sw)), _even(min(ch, sh))
    x = _even(min(max(bx + (bw - cw) / 2, 0), sw - cw))
    y = _even(min(max(by + (bh - ch) / 2, 0), sh - ch))
    below = width / (height - top_h)
    crop_w = _even(min(sw, sh * below))
    lo, hi = 0, sw - crop_w  # where the video crop's left edge may go
    cam = clip.cam_box or clip.split_cam
    if cam:  # keep the facecam's columns out of the video below, when there is room
        if cam[0] + cam[2] / 2 > 0.5:
            hi = max(lo, min(hi, _even(cam[0] * sw) - crop_w))
        else:
            lo = min(hi, max(lo, _even((cam[0] + cam[2]) * sw)))
    return {"top": [x, y, cw, ch], "top_h": top_h, "bottom_cx": clip.split_bottom if clip.split_bottom is not None else 0.5,
            "crop_w": crop_w, "crop_h": _even(min(sh, crop_w / below)), "bottom_x": [lo, hi]}


def _canvas(clip: Clip, video: Video, width: int, height: int, scale: float) -> dict:
    """Where the whole frame sits on the output: inside the padding, keeping the source shape, centred."""
    pad = round(width * clip.padding / 100)
    room_w, room_h = width - 2 * pad, height - 2 * pad
    aspect = video.width / video.height if video.height else 16 / 9
    card_w = min(room_w, round(room_h * aspect)) // 2 * 2
    card_h = min(room_h, round(card_w / aspect)) // 2 * 2
    radius = min(round(clip.radius * scale), card_w // 2, card_h // 2)
    return {"background": clip.background, "color": clip.bg_color, "color2": clip.bg_color2,
            "card_x": (width - card_w) // 2, "card_y": (height - card_h) // 2, "card_w": card_w, "card_h": card_h,
            "radius": radius}


def _logo(clip: Clip, width: int, height: int, scale: float) -> dict | None:
    size = assets.logo_size() if clip.show_logo else None
    if not size:
        return None
    w = round(width * clip.logo_size / 100) // 2 * 2
    h = max(2, round(w * size[1] / size[0]) // 2 * 2)
    margin = round(LOGO_MARGIN * scale)
    x = margin if clip.logo_corner[1] == "l" else width - w - margin
    y = margin if clip.logo_corner[0] == "t" else round(height * 0.86) - h  # bottom corners stay above the app buttons
    return {"x": x, "y": y, "w": w, "h": h}


TWEET = {"pad": 60, "top": 70, "avatar": 104, "name_size": 40, "handle_size": 34, "text_size": 48, "min_text": 34,
         "max_lines": 6, "gap": 36, "radius": 24}
TWEET_BG = {"light": "#FFFFFF", "dark": "#000000"}
LIGHT_INK, LIGHT_MUTED, X_BLUE = "#0F1419", "#536471", "#1D9BF0"


def _tweet(clip: Clip, video: Video, width: int, scale: float, handle: str, brand: str) -> dict:
    s = {k: round(v * scale) if k != "max_lines" else v for k, v in TWEET.items()}
    text = (clip.hook or clip.title).strip() or "…"
    lines, size = _fit_text(text, "Arial", s["text_size"], s["min_text"], width - 2 * s["pad"], s["max_lines"])
    line_h = round(size * 1.3)
    text_y = s["top"] + s["avatar"] + round(s["gap"] * 0.8)
    clean = "".join(handle.split())
    return {**s, "text_size": size, "line_h": line_h, "text_y": text_y, "lines": lines, "theme": clip.tweet_theme,
            "name": brand.strip() or clean.lstrip("@") or video.channel or "Clipper", "handle": clean or "@clipper",
            "height": text_y + len(lines) * line_h + s["gap"]}


def build_plan(video: Video, clip: Clip, handle: str = "", quality: int = 1, brand: str = "") -> Plan:
    """`quality` 2 = 4K: every size doubles, so text is drawn sharp at 4K instead of upscaled."""
    fps = video.fps
    by_id = {w.id: w for w in video.words}
    width, height = (side * quality for side in SIZES[clip.shape])
    tweet_mode = clip.template == "tweet"
    if tweet_mode:  # the post template always shows the whole frame as a card on the post's background
        clip = clip.model_copy(update={"framing": "fill", "background": "color", "bg_color": TWEET_BG[clip.tweet_theme],
                                       "show_hook": False})
    scale = _scale(width, height)
    vertical = clip.shape == "9:16"
    safe_px = width - 2 * (MARGIN_X_VERTICAL if vertical else MARGIN_X) * scale
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
            out_keys.append(CropKey(t=offsets[index], cx=interpolate(keys, seg_start), jump=index > 0,
                                    fit=key_at(keys, seg_start).fit))
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

    canvas = _canvas(clip, video, width, height, scale)
    tweet = _tweet(clip, video, width, scale, handle, brand) if tweet_mode else None
    if tweet:  # the card goes full width under the post; post, video and captions together sit centred
        card_w = (width - 2 * tweet["pad"]) // 2 * 2
        card_h = round(card_w * video.height / video.width) // 2 * 2 if video.width else card_w * 9 // 16 // 2 * 2
        captions_h = CAPTION_GAP * scale + line_height(look["font"], round(look["size"] * scale)) * look["max_lines"]
        top = max(0, round((height - tweet["height"] - card_h - captions_h) / 2))
        tweet = {**tweet, "y": top}
        canvas = {**canvas, "card_x": tweet["pad"], "card_y": top + tweet["height"], "card_w": card_w, "card_h": card_h,
                  "radius": tweet["radius"]}

    hook, hook_size = None, round(HOOK_STYLE["size"] * scale)
    if clip.show_hook and clip.hook.strip():
        lines, hook_size = _fit_text(clip.hook.strip(), HOOK_STYLE["font"], hook_size,
                                     round(HOOK_STYLE["min_size"] * scale), safe_px, HOOK_STYLE["max_lines"])
        hook = TextBlock(lines=lines, start=0.0, end=min(HOOK_SECONDS, duration))

    emojis: list[EmojiEvent] = []
    if clip.show_emoji:
        kept = {w.word_id: w for w in plan_words}
        for wid in sorted(i for i in clip.emoji_words if i in kept):
            if emojis and emojis[-1].end > kept[wid].start:
                emojis[-1] = emojis[-1].model_copy(update={"end": kept[wid].start})
            emojis.append(EmojiEvent(emoji=clip.emoji_words[wid], start=kept[wid].start,
                                     end=min(duration, kept[wid].start + EMOJI_HOLD_S)))
    emoji_px = round(EMOJI_PX * scale)
    caption_line = line_height(look["font"], round(look["size"] * scale))
    split = _split(clip, video, width, height) if clip.framing == "split" else {}
    caption_y = split["top_h"] / height if split else CAPTION_Y[clip.shape]  # split: captions sit on the seam
    if clip.framing == "fill":  # captions go under the picture, not across its bottom edge
        below = (canvas["card_y"] + canvas["card_h"] + CAPTION_GAP * scale + caption_line * look["max_lines"] / 2) / height
        caption_y = round(min(below if tweet else max(caption_y, below), CAPTION_Y_MAX_VERTICAL if vertical else CAPTION_Y_MAX), 4)
    emoji_y = height * caption_y - caption_line * look["max_lines"] / 2 - emoji_px * 0.7

    style = {**look, "size": round(look["size"] * scale), "outline": round(look["outline"] * scale),
             "line_px": line_height(look["font"], round(look["size"] * scale)),
             "hook": {**HOOK_STYLE, "size": hook_size, "line_px": line_height(HOOK_STYLE["font"], hook_size),
                      **({"y": SPLIT_HOOK_Y} if split else {})},
             "handle": {**HANDLE_STYLE, "size": round(HANDLE_STYLE["size"] * scale), "color": "#FFFFFF", "edge": "#000000",
                        **({"y": SAFE_SMALL_Y["handle"]} if vertical and not tweet_mode else {})},
             "credit": {**CREDIT_STYLE, "size": round(CREDIT_STYLE["size"] * scale), "color": "#FFFFFF", "edge": "#000000",
                        **({"y": SAFE_SMALL_Y["credit"]} if vertical and not tweet_mode else {})},
             "follow": {**FOLLOW_STYLE, "size": round(FOLLOW_STYLE["size"] * scale), **({"y": SPLIT_FOLLOW_Y} if split else {})}}
    if tweet and clip.tweet_theme == "light" and not look["box"]:  # dark ink on the white post, X's blue for the spoken word
        style.update(color=LIGHT_INK, active=X_BLUE if look["active"] != look["color"] else LIGHT_INK, outline=0)
        for small in ("handle", "credit"):
            style[small].update(color=LIGHT_MUTED, edge="#FFFFFF")

    captions = _caption_groups(plan_words, seg_of, look, safe_px, scale)
    zooms = (zoom_spans(offsets[1:], [g.start for g in captions], duration)
             if clip.punch_in and not tweet_mode else [])  # crop: the face; fill: the whole card; split: the lower pane

    meme_at = next((w.start for w in plan_words if w.word_id == clip.meme_word), None)
    inserts = _inserts(clip, plan_words, by_id, duration) if clip.show_broll else []
    effects = (sfx_library.place(zooms, [e.start for e in emojis], meme_at, duration, seed=clip.index,
                                 safe_only=clip.sfx_safe_only, cutaways=[i.start for i in inserts]) if clip.sfx else [])

    return Plan(
        zooms=zooms, sfx=effects,
        emphasis=[i for i in clip.emphasis_words if i in seg_of], progress=clip.show_progress,
        inserts=inserts,
        accent=look["active"] if look["active"] != look["color"] else ACCENT,
        emojis=emojis, emoji_px=emoji_px, emoji_y=round(emoji_y),
        fps=fps, width=width, height=height, duration=duration, segments=segments, words=plan_words,
        captions=captions, look=clip.look, style=style,
        caption_y=caption_y, safe_width_px=safe_px, framing=clip.framing,
        source_width=video.width, source_height=video.height, crop_w=crop_w, crop_h=crop_h,
        crop_keys=out_keys, hook=hook, canvas=canvas,
        split=split,
        music={"file": clip.music, "volume": clip.music_volume / 100, "peak": clip.music_peak / 100,
               "riser_s": float(min(clip.riser_s, duration))} if clip.music else None,
        logo=_logo(clip, width, height, scale),
        tweet=tweet,
        follow=TextBlock(lines=[f"Follow {''.join(handle.split())} for more" if handle.strip() else "Follow for more"],
                         start=max(0.0, duration - FOLLOW_SECONDS), end=duration) if clip.show_follow else None,
        fit_spans=_fit_spans(out_keys, duration) if clip.framing == "crop" and crop_w < video.width else [],
        handle="".join(handle.split()) if clip.show_handle else "",
        credit=f"Credit: {video.channel}" if clip.show_credit and video.channel else "",
        beeps=beeps,
    )
