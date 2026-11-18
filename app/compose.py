"""The whole clip as one HyperFrames composition: the cut source video framed the way the plan says (follow the face
with punch-ins, whole frame on a canvas, or split), Remotion's inserts, the captions and graphics layer
(web/overlay/), logo, post header, and every sound (voice, bleeps, music ducked under the speech, effects).
HyperFrames renders it to the final MP4; ffmpeg is not used to build the clip.

Times in the page are output seconds. Media times are seconds into the file the page plays (`media/source.mp4`).
"""

import html
import json
import math
import os
import shutil
import struct
import wave
from pathlib import Path

from app import ROOT, assets, sfx
from app.emoji import emoji_png
from app.plan import FONTS, Insert, Plan

OVERLAY_DIR = ROOT / "web" / "overlay"
FONT_NAMES = {"arial-black.ttf": FONTS["Arial Black"], "arial-bold.ttf": FONTS["Arial Bold"]}
FILL_BLUR_PX = 30
GRADE = "contrast(1.07) saturate(.95) brightness(.98)"   # a light grade on the footage (video-edit skill, 'bold' look)
CUT_PUNCH = 1.05        # every jump cut lands slightly zoomed and settles: the cut reads as an edit, not a glitch
NUDGE_S = 0.002         # a tl.set on a cut lands this much early, or float rounding shows it one frame late
MUSIC_STEP_S = 0.5      # the music's volume (ducking, riser, fades) changes in steps this long
DUCK = 0.2              # music level while someone speaks: about -14 dB under its own level (video-editor music rules)
BEEP_HZ, BEEP_GAIN = 1000, 0.25
SOURCE = "media/source.mp4"
MAX_VOLUME = 2.5        # HyperFrames honours gains above 1; speech is boosted up to this to reach -14 LUFS


def _video(seg_id: str, start: float, duration: float, media_start: float, track: int, src: str = SOURCE) -> str:
    return (f'<video id="{seg_id}" class="seg" data-start="{start:.6f}" data-duration="{duration:.6f}" '
            f'data-media-start="{media_start:.6f}" data-track-index="{track}" src="{src}" muted playsinline></video>')


def _audio(audio_id: str, start: float, duration: float, media_start: float, track: int, src: str, volume: float) -> str:
    return (f'<audio id="{audio_id}" data-start="{start:.6f}" data-duration="{duration:.6f}" data-media-start="{media_start:.6f}" '
            f'data-track-index="{track}" data-volume="{min(max(volume, 0.0), MAX_VOLUME):.3f}" src="{src}"></audio>')


def segment_times(plan: Plan, frame_offset: int) -> list[tuple[float, float, float]]:
    """(output start, length, media start) of every kept run, in seconds."""
    out, cursor = [], 0
    for s in plan.segments:
        frames = s.end_frame - s.start_frame
        # ponytail: HyperFrames snaps clip media to the source frame grid and samples with fps round=up, so a cut can
        # land one frame late (a held frame in a speech pause). Exact cuts would need pre-cut files per segment.
        out.append((cursor / plan.fps, frames / plan.fps, (s.start_frame - frame_offset) / plan.fps))
        cursor += frames
    return out


def _segments(prefix: str, segs, track: int) -> str:
    return "".join(_video(f"{prefix}{n}", a, d, m, track) for n, (a, d, m) in enumerate(segs))


def crop_states(plan: Plan) -> list[list[float]]:
    """[t, x, y, scale] for the follow-face wrapper, one per output frame where it changes: the crop centre glides or
    jumps between keys, punch-ins narrow it around (centre, zoom_anchor_y). Mirrors web/preview.js draw()."""
    sw, sh, k = plan.source_width, plan.source_height, plan.width / plan.crop_w
    base_y = (sh - plan.crop_h) / 2
    states, last = [], None
    for n in range(round(plan.duration * plan.fps)):
        t = n / plan.fps
        z = plan.zoom if any(a <= t < b for a, b in plan.zooms) else 1.0
        w, h = plan.crop_w / z, plan.crop_h / z
        left = min(max(plan.crop_at(t) * sw - plan.crop_w / 2 + (plan.crop_w - w) / 2, 0), sw - w)
        top = base_y + (plan.crop_h - h) * plan.zoom_anchor_y
        state = [round(-left * k * z, 2), round(-top * k * z, 2), round(z, 4)]
        if state != last:
            states.append([round(t, 6), *state])
            last = state
    return states


def _fit_view(plan: Plan, segs, track: int, visible: bool) -> str:
    """Whole frame as a card on the canvas: a blurred copy of itself, a colour or a gradient behind."""
    c, w, h, sw, sh = plan.canvas, plan.width, plan.height, plan.source_width, plan.source_height
    parts = []
    if c["background"] == "blur":
        s = max(w / sw, h / sh)
        parts.append(f'<div class="wrap" style="left:{(w - sw * s) / 2:.2f}px;top:{(h - sh * s) / 2:.2f}px;width:{sw * s:.2f}px;'
                     f'height:{sh * s:.2f}px;filter:blur({FILL_BLUR_PX}px)">{_segments("fb", segs, track)}</div>')
        track += 1
        ground = "#000"
    elif c["background"] == "gradient":
        ground = f'linear-gradient(to bottom, {c["color"]}, {c["color2"]})'
    else:
        ground = c["color"]
    parts.append(f'<div class="wrap" style="left:{c["card_x"]}px;top:{c["card_y"]}px;width:{c["card_w"]}px;height:{c["card_h"]}px;'
                 f'border-radius:{c.get("radius", 0)}px;overflow:hidden">{_segments("fc", segs, track)}</div>')
    return (f'<div id="fit" class="view" style="background:{ground};opacity:{1 if visible else 0};'
            f'transform-origin:50% {plan.zoom_anchor_y * 100:.1f}%">' + "".join(parts) + "</div>")


def _split_view(plan: Plan, segs, track: int) -> str:
    """Facecam crop on top, the video below (mirrors web/preview.js drawSplit)."""
    sp, w, h, sw, sh = plan.split, plan.width, plan.height, plan.source_width, plan.source_height
    x, y, cw, ch = sp["top"]
    top_h = sp["top_h"]
    sx, sy = w / cw, top_h / ch
    top = (f'<div class="pane" style="top:0;height:{top_h}px"><div class="wrap" style="left:{-x * sx:.2f}px;top:{-y * sy:.2f}px;'
           f'width:{sw * sx:.2f}px;height:{sh * sy:.2f}px">{_segments("st", segs, track)}</div></div>')
    lo, hi = sp["bottom_x"]
    bx = min(max(round(sp["bottom_cx"] * sw - sp["crop_w"] / 2), lo), hi) // 2 * 2
    by = (sh - sp["crop_h"]) // 2 // 2 * 2
    bsx, bsy = w / sp["crop_w"], (h - top_h) / sp["crop_h"]
    bottom = (f'<div class="pane" style="top:{top_h}px;height:{h - top_h}px"><div id="sbz" class="view" style="transform-origin:50% 50%">'
              f'<div class="wrap" style="left:{-bx * bsx:.2f}px;top:{-by * bsy:.2f}px;width:{sw * bsx:.2f}px;height:{sh * bsy:.2f}px">'
              f'{_segments("sb", segs, track + 1)}</div></div></div>')
    return f'<div id="split" class="view">{top}{bottom}</div>'


def _beep_file(folder: Path) -> Path:
    """A one-second 1 kHz tone (cut to each bleep's length by its clip), written without ffmpeg."""
    path = folder / "beep.wav"
    rate = 48000
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(rate)
        out.writeframes(b"".join(struct.pack("<h", round(32767 * BEEP_GAIN * math.sin(2 * math.pi * BEEP_HZ * n / rate)))
                                 for n in range(rate)))
    return path


def _minus(a: float, b: float, holes: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """[a, b) with the holes taken out."""
    parts, cur = [], a
    for x, y in sorted(holes):
        if y <= cur or x >= b:
            continue
        if x > cur:
            parts.append((cur, x))
        cur = max(cur, y)
    if cur < b:
        parts.append((cur, b))
    return [(x, y) for x, y in parts if y - x > 1e-3]


def voice_audio(plan: Plan, segs, gain: float, track: int, voice: tuple[str, float] = (SOURCE, 0.0)) -> str:
    """The speech, cut like the picture; bleeped spans are silent (the beep plays there instead). `voice` is the file
    the speech plays from and the media second it starts at: the levelled speech track, or the source itself."""
    src, origin = voice
    clips = []
    for n, (start, length, media) in enumerate(segs):
        for m, (a, b) in enumerate(_minus(start, start + length, plan.beeps)):
            clips.append(_audio(f"v{n}_{m}", a, b - a, media - origin + (a - start), track, src, gain))
    for n, (a, b) in enumerate(plan.beeps):
        clips.append(_audio(f"beep{n}", a, min(b - a, 1.0), 0, track + 1, "media/beep.wav", 1.0))
    return "".join(clips)


def music_audio(plan: Plan, length: float, track: int) -> str:
    """The track looped under the speech in MUSIC_STEP_S steps: ducked while someone speaks, rising to its peak over
    the last riser seconds, faded in and out."""
    m, d = plan.music, plan.duration
    clips, t, n = [], 0.0, 0
    while t < d - 1e-6:
        end = min(t + MUSIC_STEP_S, d)
        level = m["volume"]
        if m["riser_s"] > 0 and t >= d - m["riser_s"]:
            level = m["volume"] + (m["peak"] - m["volume"]) * (t - (d - m["riser_s"])) / m["riser_s"]
        speaking = any(w.start < end and w.end > t for w in plan.words)
        fade = min(1.0, (t + MUSIC_STEP_S / 2) / 0.5, (d - t) / 0.5)
        volume = level * (DUCK if speaking else 1.0) * fade
        media = t % length
        span = min(end - t, length - media)  # a step that crosses the loop point ends there; the next one starts at 0
        clips.append(_audio(f"m{n}", t, span, media, track, "media/music" + Path(m["file"]).suffix, volume))
        t, n = t + span, n + 1
    return "".join(clips)


def _copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        shutil.copyfile(source, target)


def write(plan: Plan, source: Path, frame_offset: int, folder: Path, inserts: list[tuple[Insert, Path]],
          voice_gain: float = 1.0, tweet_png: Path | None = None, voice_wav: Path | None = None) -> Path:
    """Write the HyperFrames project for this clip into folder/clip/ and return it."""
    project = folder / "clip"
    if project.exists():
        shutil.rmtree(project)
    (project / "media").mkdir(parents=True)
    os.symlink(source.resolve(), project / SOURCE)  # the source can be gigabytes: never copied
    for name in ("overlay.js", "overlay.css", "gsap.min.js"):
        _copy(OVERLAY_DIR / name, project / "overlay" / name)
    for name, font in FONT_NAMES.items():
        _copy(font, project / "fonts" / name)
    segs = segment_times(plan, frame_offset)
    w, h = plan.width, plan.height
    views, script = [], []
    track = 0
    def punch_ins(selector: str) -> None:  # whole-frame and split clips zoom their picture on the same beats
        for a, b in plan.zooms:
            script.append(f'tl.set("{selector}", {{scale: {plan.zoom}}}, {a:.6f}); tl.set("{selector}", {{scale: 1}}, {b:.6f});')

    if plan.framing == "split":
        views.append(_split_view(plan, segs, track)); track += 2
        punch_ins("#sbz")
    else:
        if plan.framing == "crop":
            k = w / plan.crop_w
            views.append(f'<div id="crop" class="wrap" style="width:{plan.source_width * k:.2f}px;height:{plan.source_height * k:.2f}px">'
                         f'{_segments("c", segs, track)}</div>')
            track += 1
            script.append(f"crop({json.dumps(crop_states(plan))});")
        if plan.framing == "fill" or plan.fit_spans:
            views.append(_fit_view(plan, segs, track, visible=plan.framing == "fill")); track += 2
            if plan.framing == "fill":
                punch_ins("#fit")
            for a, b in plan.fit_spans:
                script.append(f'tl.set("#fit", {{opacity: 1}}, {max(a - NUDGE_S, 0):.6f}); tl.set("#fit", {{opacity: 0}}, {b - NUDGE_S:.6f});')
    for start, _, _ in segs[1:]:
        script.append(f'tl.fromTo("#stage", {{scale: {CUT_PUNCH}}}, {{scale: 1, duration: 0.32, ease: "power2.out", '
                      f'immediateRender: false}}, {start - NUDGE_S:.6f});')
    cutaways = []
    for n, (insert, path) in enumerate(inserts):
        _copy(path, project / "inserts" / f"{n}.mp4")
        cutaways.append(f'<div id="ins{n}" class="view insert" style="opacity:0">'
                        f'{_video(f"insv{n}", insert.start, insert.end - insert.start, 0, track, f"inserts/{n}.mp4")}</div>')
        script.append(f'tl.set("#ins{n}", {{opacity: 1}}, {insert.start - NUDGE_S:.6f}); '
                      f'tl.fromTo("#ins{n}", {{scale: 1.06}}, {{scale: 1, duration: 0.25, ease: "power2.out"}}, {insert.start:.6f}); '
                      f'tl.set("#ins{n}", {{opacity: 0}}, {insert.end - NUDGE_S:.6f});')
        track += 1
    pictures = []
    if plan.tweet and tweet_png:
        _copy(tweet_png, project / "media" / "tweet.png")
        pictures.append(f'<img class="still" src="media/tweet.png" alt="" style="left:0;top:{plan.tweet["y"]}px;width:{w}px;'
                        f'height:{plan.tweet["height"]}px">')
    if plan.logo and assets.LOGO_FILE.is_file():
        _copy(assets.LOGO_FILE, project / "media" / "logo.png")
        lg = plan.logo
        pictures.append(f'<img class="still" src="media/logo.png" alt="" style="left:{lg["x"]}px;top:{lg["y"]}px;width:{lg["w"]}px;'
                        f'height:{lg["h"]}px">')
    if voice_wav:  # the levelled speech covers the source from the clip's first kept frame on
        _copy(voice_wav, project / "media" / "voice.wav")
        sounds = [voice_audio(plan, segs, 1.0, 20, ("media/voice.wav", segs[0][2]))]
    else:
        sounds = [voice_audio(plan, segs, voice_gain, 20)]
    if plan.beeps:
        _beep_file(project / "media")
    if plan.music:
        track_file = assets.music_path(plan.music["file"])
        _copy(track_file, project / "media" / ("music" + track_file.suffix))
        sounds.append(music_audio(plan, assets._duration(track_file) or plan.duration, 22))
    for n, event in enumerate(plan.sfx):
        file = sfx.path_of(event["file"])
        _copy(file, project / "sfx" / file.name)
        length = min(assets._duration(file) or 1.0, plan.duration - event["t"])
        sounds.append(_audio(f"fx{n}", event["t"], length, 0, 30 + n, f"sfx/{file.name}", event["gain"]))
    pictures_of = {}
    for emoji in dict.fromkeys(e.emoji for e in plan.emojis):
        png = emoji_png(emoji, plan.emoji_px, project / "emoji")
        pictures_of[emoji] = f"emoji/{png.name}"
    data = json.dumps({**plan.model_dump(mode="json"), "emoji_images": pictures_of}, ensure_ascii=False).replace("</", "<\\/")
    page = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{html.escape(plan.look)}</title>
<link rel="stylesheet" href="overlay/overlay.css">
<style>
  #clip {{ position: relative; width: {w}px; height: {h}px; overflow: hidden; background: #000; }}
  #clip .view {{ position: absolute; inset: 0; overflow: hidden; }}
  #clip .wrap {{ position: absolute; left: 0; top: 0; transform-origin: 0 0; }}
  #clip .pane {{ position: absolute; left: 0; width: {w}px; overflow: hidden; }}
  #clip .seg {{ position: absolute; inset: 0; width: 100%; height: 100%; object-fit: fill; }}
  #clip .insert video {{ position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }}
  #clip .still {{ position: absolute; }}
  #text {{ position: absolute; inset: 0; }}
  #clip .shade {{ position: absolute; inset: 0; pointer-events: none;
    background: radial-gradient(ellipse at 50% 45%, transparent 58%, rgba(0,0,0,.32) 100%),
                linear-gradient(to bottom, rgba(0,0,0,.22), transparent 16%, transparent 64%, rgba(0,0,0,.26)); }}
</style></head>
<body>
<div id="clip" data-composition-id="clip" data-start="0" data-duration="{plan.duration:.6f}" data-width="{w}" data-height="{h}">
<div id="stage" class="view" style="filter:{GRADE};transform-origin:50% {plan.zoom_anchor_y * 100:.1f}%">{"".join(views)}</div>
<div class="shade"></div>
{"".join(cutaways)}
<div id="text"></div>
{"".join(pictures)}
{"".join(sounds)}
</div>
<script src="overlay/gsap.min.js"></script>
<script src="overlay/overlay.js"></script>
<script>
  const P = {data};
  const tl = gsap.timeline({{ paused: true }});
  function crop(states) {{ states.forEach(([t, x, y, s]) => tl.set("#crop", {{ x, y, scale: s }}, t)); }}
  {"".join(script)}
  window.clipOverlay.build(P, {{ root: document.getElementById("text"), tl }});
  window.__timelines = window.__timelines || {{}};
  window.__timelines["clip"] = tl;
</script>
</body></html>
"""
    (project / "index.html").write_text(page, encoding="utf-8")
    return project
