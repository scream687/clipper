"""Render a plan to MP4 with one ffmpeg command.

Cuts select source FRAME NUMBERS and the speech is trimmed at the same frame-aligned times, so picture and sound
stay in step across any number of cuts. Framing (follow the face, whole frame on a canvas, facecam split), punch-ins
and the small zoom that lands every cut are one crop whose size and place change per frame (sendcmd), scaled back to
the output size. On top: a light grade and vignette, Remotion's B-roll inserts (the only part not made here), the post
header, the captions and text (app/ass.py), emojis, logo and progress bar. The sound is the levelled speech with
bleeps, music ducked under it (sidechain) with its riser and fades, and the sound effects.
"""

import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image

from app import DATA_DIR, FFMPEG, assets, inserts as cutaways, sfx
from app.ass import build_ass
from app.emoji import emoji_png
from app.loudness import integrated_lufs, level_speech
from app.media import partial_path
from app.plan import FONT_DIR, Plan

LOUDNESS_LUFS = -14          # where Reels and Shorts play
GAIN_RANGE = (0.3, 2.5)      # never boost speech more than ~8 dB
PEAK_CEILING_DB = -1.0
JOIN_FADE_S = 0.008          # a tiny fade at every audio join so jump cuts do not click
BEEP_HZ, BEEP_VOLUME = 1000, 0.25
FILL_BLUR = 30
CUT_PUNCH, CUT_PUNCH_S = 1.05, 0.32   # every jump cut lands slightly zoomed and settles: it reads as an edit
# a light grade (video-edit skill, 'bold' look): contrast 1.07, saturation 0.95, a touch darker. A lookup table,
# not eq: the same look at a fifth of the cost (eq added 20 s to a 67 s 1080p60 clip, this 4 s)
GRADE = "lutyuv=y='clip((val-128)*1.07+125.5,0,255)':u='(val-128)*0.95+128':v='(val-128)*0.95+128'"
BITRATE_1080 = 12            # Mbit/s at 1080x1920 30 fps; scaled by pixels and frame rate
TIMEOUT_S = 3600


def voice_gain(plan: Plan, source: Path, frame_offset: int) -> float:
    """The speech volume that lands the clip at LOUDNESS_LUFS, measured over the source span the clip covers."""
    first, last = plan.segments[0].start_frame - frame_offset, plan.segments[-1].end_frame - frame_offset
    measured = integrated_lufs(source, first / plan.fps, (last - first) / plan.fps)
    if measured is None:
        return 1.0
    level, peak = measured
    wanted_db = min(LOUDNESS_LUFS - level, PEAK_CEILING_DB - peak)  # loud enough, never clipping
    return min(max(10 ** (wanted_db / 20), GAIN_RANGE[0]), GAIN_RANGE[1])


def _rate(fps: float) -> str:
    """A frame rate as ffmpeg wants it: 30000/1001, not 29.97002997."""
    rate = Fraction(fps).limit_denominator(1001)
    return str(rate.numerator) if rate.denominator == 1 else f"{rate.numerator}/{rate.denominator}"


def _esc(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace(",", "\\,")


def cut_times(plan: Plan) -> list[float]:
    """Output seconds where each kept run after the first starts (the jump cuts)."""
    out, cursor = [], 0
    for s in plan.segments[:-1]:
        cursor += s.end_frame - s.start_frame
        out.append(cursor / plan.fps)
    return out


def scale_at(plan: Plan, t: float, cuts: list[float], zoom: bool, punch: bool) -> float:
    """How far the picture is pushed in at t: the punch-in on alternate shots, times the settle after a cut. Pure."""
    z = plan.zoom if zoom and any(a <= t < b for a, b in plan.zooms) else 1.0
    p = 1.0
    for c in cuts if punch else ():
        if c <= t < c + CUT_PUNCH_S:
            p = 1 + (CUT_PUNCH - 1) * (1 - (t - c) / CUT_PUNCH_S) ** 2  # power2.out, like the preview
    return z * p


def crop_boxes(plan: Plan, box_at: Callable[[float], tuple[float, float, float, float]]) -> list[tuple[float, list[int]]]:
    """(t, [x, y, w, h]) for every output frame where the crop changes. Pure."""
    out, last = [], None
    for n in range(round(plan.duration * plan.fps)):
        t = n / plan.fps
        x, y, w, h = box_at(t)
        box = [int(round(x / 2) * 2), int(round(y / 2) * 2), max(int(round(w / 2) * 2), 2), max(int(round(h / 2) * 2), 2)]
        if box != last:
            out.append((t, box))
            last = box
    return out


def _cropper(name: str, boxes: list[tuple[float, list[int]]], work: Path) -> str:
    """sendcmd + crop filters that move and resize the crop on the frames where it changes."""
    lines = [f"{max(t - 0.0005, 0):.6f} crop@{name} w {w}, crop@{name} h {h}, crop@{name} x {x}, crop@{name} y {y};"
             for t, (x, y, w, h) in boxes]
    cmd = work / f"{name}.cmd"
    cmd.write_text("\n".join(lines) + "\n")
    x, y, w, h = boxes[0][1]
    return f"sendcmd=f={_esc(cmd)},crop@{name}=w={w}:h={h}:x={x}:y={y}:exact=1"


def face_box(plan: Plan, cuts: list[float]) -> Callable[[float], tuple[float, float, float, float]]:
    """Follow the face: the crop centre glides or jumps between keys, punch-ins narrow it towards zoom_anchor_y."""
    sw, sh = plan.source_width, plan.source_height
    base_y = (sh - plan.crop_h) / 2

    def at(t: float):
        k = scale_at(plan, t, cuts, zoom=True, punch=True)
        w, h = plan.crop_w / k, plan.crop_h / k
        x = min(max(plan.crop_at(t) * sw - w / 2, 0), sw - w)
        return x, base_y + (plan.crop_h - h) * plan.zoom_anchor_y, w, h
    return at


def stage_box(plan: Plan, cuts: list[float], zoom: bool, width: int, height: int, anchor_y: float):
    """A push-in on an already framed picture (the canvas, or the split screen), anchored at (50 %, anchor_y)."""
    def at(t: float):
        k = scale_at(plan, t, cuts, zoom=zoom, punch=True)
        w, h = width / k, height / k
        return (width - w) / 2, (height - h) * anchor_y, w, h
    return at


def _canvas(plan: Plan, src: str, out: str, n: int) -> str:
    """The whole frame as a card on the canvas: a blurred copy of itself, a colour or a gradient behind."""
    W, H, D, c = plan.width, plan.height, plan.duration, plan.canvas
    cx, cy = c.get("card_x", 0), c.get("card_y", 0)
    cw, ch = c.get("card_w", W), c.get("card_h", H)
    fit = f"[{src}]split[bg{n}][fg{n}];[fg{n}]scale={cw}:{ch}:flags=lanczos,setsar=1[card{n}];"
    if c.get("background", "blur") == "blur":
        ground = (f"[bg{n}]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                  f"gblur=sigma={FILL_BLUR},setsar=1[ground{n}];")
    else:
        top, bottom = c.get("color", "#000000"), c.get("color2", c.get("color", "#000000"))
        source = (f"gradients=s={W}x{H}:c0={top}:c1={bottom}:x0=0:y0=0:x1=0:y1={H}:speed=0:d={D:.6f}:r={_rate(plan.fps)}"
                  if c.get("background") == "gradient" else f"color=c={top}:s={W}x{H}:d={D:.6f}:r={_rate(plan.fps)}")
        ground = f"[bg{n}]nullsink;{source},format=yuv420p,setsar=1[ground{n}];"
    return fit + ground + f"[ground{n}][card{n}]overlay={cx}:{cy}:shortest=1[{out}]"


def _shade(width: int, height: int) -> Path:
    """The vignette as a picture: darker corners and a soft top and bottom, so text reads on any footage."""
    path = DATA_DIR / "shade" / f"{width}x{height}.png"
    if path.is_file():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    y, x = np.mgrid[0:height, 0:width]
    r = np.hypot((x / width - 0.5) / 0.5, (y / height - 0.45) / 0.55)
    radial = np.clip((r - 0.58) / 0.42, 0, 1) * 0.32
    v = y / height
    linear = np.where(v < 0.16, 0.22 * (1 - v / 0.16), np.where(v > 0.64, 0.26 * (v - 0.64) / 0.36, 0))
    alpha = 1 - (1 - radial) * (1 - linear)
    rgba = np.zeros((height, width, 4), np.uint8)
    rgba[..., 3] = (alpha * 255).astype(np.uint8)
    Image.fromarray(rgba, "RGBA").save(path)
    return path


def _framing(plan: Plan, work: Path) -> str:
    """[src] -> [stage]: the picture framed and moving the way the plan says."""
    W, H, sw, sh = plan.width, plan.height, plan.source_width, plan.source_height
    cuts = cut_times(plan)
    if plan.framing == "split":
        sp = plan.split
        x, y, cw, ch = sp["top"]
        top_h = sp["top_h"]
        lo, hi = sp["bottom_x"]
        bx = min(max(round(sp["bottom_cx"] * sw - sp["crop_w"] / 2), lo), hi) // 2 * 2
        by = (sh - sp["crop_h"]) // 2 // 2 * 2

        def bottom(t: float):  # the punch-in is on the lower pane only, about its centre
            k = scale_at(plan, t, cuts, zoom=True, punch=False)
            w, h = sp["crop_w"] / k, sp["crop_h"] / k
            return bx + (sp["crop_w"] - w) / 2, by + (sp["crop_h"] - h) / 2, w, h
        return (f"[src]split[top][low];[top]crop={cw}:{ch}:{x}:{y}:exact=1,scale={W}:{top_h}:flags=lanczos,setsar=1[topv];"
                f"[low]{_cropper('low', crop_boxes(plan, bottom), work)},scale={W}:{H - top_h}:flags=lanczos,setsar=1[lowv];"
                f"[topv][lowv]vstack,{_cropper('stage', crop_boxes(plan, stage_box(plan, cuts, False, W, H, plan.zoom_anchor_y)), work)},"
                f"scale={W}:{H}:flags=bicubic,setsar=1[stage]")
    if plan.framing == "fill":
        return (_canvas(plan, "src", "fit", 0) + ";"
                f"[fit]{_cropper('stage', crop_boxes(plan, stage_box(plan, cuts, True, W, H, plan.zoom_anchor_y)), work)},"
                f"scale={W}:{H}:flags=bicubic,setsar=1[stage]")
    face = f"{_cropper('face', crop_boxes(plan, face_box(plan, cuts)), work)},scale={W}:{H}:flags=lanczos,setsar=1"
    if not plan.fit_spans:
        return f"[src]{face}[stage]"
    # Faceless wide shots inside a cropped clip: the whole frame on its canvas during those spans.
    when = "+".join(f"between(t,{a:.6f},{b - 0.5 / plan.fps:.6f})" for a, b in plan.fit_spans)
    return f"[src]split[fsrc][csrc];[csrc]{face}[faced];{_canvas(plan, 'fsrc', 'fit', 0)};[faced][fit]overlay=0:0:enable='{when}'[stage]"


def _bitrate(plan: Plan, fps: float) -> str:
    return f"{max(6, round(BITRATE_1080 * plan.width * plan.height / (1080 * 1920) * max(fps, 30) / 30))}M"


def render(plan: Plan, source: Path, out: Path, log: Callable[[str], None], frame_offset: int = 0,
           out_fps: float | None = None) -> Path:
    """`frame_offset` is the source frame that `source` starts at (0 for the full video, the window start for a mezzanine)."""
    if not plan.segments:
        raise RuntimeError("This clip has no words left to render.")
    first, last = plan.segments[0].start_frame - frame_offset, plan.segments[-1].end_frame - frame_offset
    if first < 0:
        raise RuntimeError("This clip now reaches outside the part of the video that was kept.")
    if not source.is_file():
        raise RuntimeError(f"The video for this clip is missing ({source.name}); add the video again.")
    music_file = None
    if plan.music:
        try:
            music_file = assets.music_path(plan.music["file"])
        except KeyError:
            raise RuntimeError(f"The music track {plan.music['file']} is no longer in the library. Pick another one.") from None
    work = out.parent
    work.mkdir(parents=True, exist_ok=True)
    W, H, D, fps = plan.width, plan.height, plan.duration, plan.fps
    inserts = cutaways.render_all(plan, source, frame_offset / fps, work / "inserts", log)  # Remotion, when installed
    (work / "captions.ass").write_text(build_ass(plan), encoding="utf-8")

    # Inputs: 0 the source (from half a frame before the first kept frame, so frame numbers count from it).
    args = ["-ss", f"{max(0.0, (first - 0.5) / fps):.6f}", "-to", f"{(last + 0.5) / fps:.6f}", "-i", str(source)]
    def add(*more: str) -> int:
        args.extend(more)
        return sum(1 for a in args if a == "-i") - 1

    graph: list[str] = []
    keep = "+".join(f"between(n,{s.start_frame - frame_offset - first},{s.end_frame - frame_offset - first - 1})"
                    for s in plan.segments)
    graph.append(f"[0:v]select='{keep}',setpts=N/({fps})/TB,setsar=1[src]")
    graph.append(_framing(plan, work))
    def still(path: Path, name: str, size: str = "") -> str:
        """A picture decoded and converted once, then repeated (a looped input decodes it on every frame)."""
        i = add("-i", str(path))
        graph.append(f"[{i}:v]{f'scale={size},' if size else ''}format=yuva420p,loop=loop=-1:size=1:start=0[{name}]")
        return f"[{name}]"

    graph.append(f"[stage]{GRADE},format=yuv420p[graded]")
    graph.append(f"[graded]{still(_shade(W, H), 'shade')}overlay=0:0:eof_action=pass[v0]")
    label, n = "v0", 0

    def over(inp: str, x: str, y: str, when: str = "") -> None:
        nonlocal label, n
        n += 1
        enable = f":enable='{when}'" if when else ""
        graph.append(f"[{label}]{inp}overlay=x={x}:y={y}:eof_action=pass{enable}[v{n}]")
        label = f"v{n}"

    for k, (insert, path) in enumerate(inserts):
        i = add("-i", str(path))
        graph.append(f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,fps={_rate(fps)},"
                     f"setpts=PTS-STARTPTS+{insert.start:.6f}/TB[ins{k}]")
        over(f"[ins{k}]", "0", "0", f"between(t,{insert.start:.6f},{insert.end - 0.5 / fps:.6f})")
    if plan.tweet:
        from app.template import tweet_header
        tweet_header(plan).save(work / "tweet.png")
        over(still(work / "tweet.png", "tw", f"{W}:{plan.tweet['height']}"), "0", str(plan.tweet["y"]))
    graph.append(f"[{label}]ass=filename={_esc(work / 'captions.ass')}:fontsdir={_esc(FONT_DIR)}[vt]")
    label = "vt"
    for k, emoji in enumerate(dict.fromkeys(e.emoji for e in plan.emojis)):
        events = [e for e in plan.emojis if e.emoji == emoji]
        pic = still(emoji_png(emoji, plan.emoji_px, DATA_DIR / "emoji"), f"em{k}")
        graph.append(f"{pic}split={len(events)}" + "".join(f"[em{k}_{m}]" for m in range(len(events))))
        for m, event in enumerate(events):
            over(f"[em{k}_{m}]", "(W-w)/2", str(round(plan.emoji_y - plan.emoji_px / 2)),
                 f"between(t,{event.start:.6f},{event.end:.6f})")
    if plan.logo and assets.LOGO_FILE.is_file():
        lg = plan.logo
        over(still(assets.LOGO_FILE, "logo", f"{lg['w']}:{lg['h']}"), str(lg["x"]), str(lg["y"]))
    if plan.progress:  # a bar across the top edge, filling as the clip plays
        bar = max(4, round(W / 135))
        graph.append(f"color=c={plan.accent}:s={W}x{bar}:d={D:.6f}:r={_rate(fps)}[bar]")
        over("[bar]", f"-w+w*t/{D:.6f}", "0")
    rate = out_fps or fps
    # a new frame rate: hold the last frame a little so the converted clip is not short (-frames:v trims the rest)
    convert = f"tpad=stop_mode=clone:stop_duration=0.5,fps={_rate(rate)}," if abs(rate - fps) > 1e-3 else ""
    graph.append(f"[{label}]{convert}format=yuv420p[v]")

    # Sound: the levelled speech (compressor + limiter to -14 LUFS) cut like the picture, else the source with a gain.
    try:
        voice = level_speech(source, first / fps, (last - first) / fps, work / "voice.wav", LOUDNESS_LUFS)
        vin, gain = f"{add('-i', str(voice))}:a", 1.0
    except RuntimeError as error:
        log(f"{error} Using the original speech with a safe gain.")
        vin, gain = "0:a", voice_gain(plan, source, frame_offset)
    origin = plan.segments[0].start_frame
    parts = []
    for k, s in enumerate(plan.segments):
        a, b = (s.start_frame - origin) / fps, (s.end_frame - origin) / fps
        fade = min(JOIN_FADE_S, (b - a) / 4)
        graph.append(f"[{vin}]atrim={a:.6f}:{b:.6f},asetpts=PTS-STARTPTS,"
                     f"afade=t=in:d={fade:.4f},afade=t=out:st={b - a - fade:.6f}:d={fade:.4f}[a{k}]")
        parts.append(f"[a{k}]")
    speech = f"{''.join(parts)}concat=n={len(parts)}:v=0:a=1,aformat=sample_rates=48000:channel_layouts=stereo,volume={gain:.4f}"
    if plan.beeps:
        spans = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in plan.beeps)
        speech += f",volume=0:enable='{spans}'"
        graph.append(f"aevalsrc='{BEEP_VOLUME}*sin(2*PI*{BEEP_HZ}*t)*({spans})':s=48000:d={D:.6f},"
                     f"aformat=channel_layouts=stereo[beep]")
    graph.append(f"{speech},apad,atrim=0:{D:.6f}[speech]")
    mix = ["[speech]"]
    if plan.beeps:
        mix.append("[beep]")
    if music_file:  # looped, at its level, rising over the riser, faded, and ducked under the speech by a sidechain
        m = plan.music
        i = add("-stream_loop", "-1", "-i", str(music_file))
        level = (f"if(gte(t,{D - m['riser_s']:.6f}),{m['volume']}+({m['peak']}-{m['volume']})*(t-{D - m['riser_s']:.6f})/{m['riser_s']:.6f},{m['volume']})"
                 if m["riser_s"] > 0 else str(m["volume"]))
        graph.append(f"[speech]asplit[speech0][key]")
        mix[0] = "[speech0]"
        graph.append(f"[{i}:a]atrim=0:{D:.6f},asetpts=PTS-STARTPTS,aformat=sample_rates=48000:channel_layouts=stereo,"
                     f"volume='{level}':eval=frame,afade=t=in:d=0.5,afade=t=out:st={max(D - 0.5, 0):.6f}:d=0.5[bed]")
        graph.append("[bed][key]sidechaincompress=threshold=0.02:ratio=8:attack=20:release=350:makeup=1[music]")
        mix.append("[music]")
    for k, event in enumerate(plan.sfx):
        i = add("-i", str(sfx.path_of(event["file"])))
        ms = round(event["t"] * 1000)
        graph.append(f"[{i}:a]aformat=sample_rates=48000:channel_layouts=stereo,volume={event['gain']},adelay={ms}|{ms}[fx{k}]")
        mix.append(f"[fx{k}]")
    graph.append(f"{''.join(mix)}amix=inputs={len(mix)}:normalize=0:duration=first,alimiter=limit=0.95:level=0,"
                 f"atrim=0:{D:.6f}[a]" if len(mix) > 1 else f"[speech]alimiter=limit=0.95:level=0[a]")

    partial = partial_path(out)
    cmd = [FFMPEG, "-y", "-v", "error", *args, "-filter_complex", ";".join(graph), "-map", "[v]", "-map", "[a]",
           "-frames:v", str(round(D * rate)), "-c:v", "h264_videotoolbox", "-b:v", _bitrate(plan, rate), "-profile:v", "high",
           "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(partial)]
    log(f"Rendering {D:.1f}s at {W}x{H}...")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S)
    if result.returncode != 0 or not partial.is_file():
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg could not render this clip: {result.stderr.strip()[-600:]}")
    partial.replace(out)
    return out
