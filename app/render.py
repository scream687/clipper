"""Render a plan to MP4 with one ffmpeg command.

Cuts select source FRAME NUMBERS (not times) and the audio is trimmed at the same
frame-aligned times, so picture and sound stay in sync across any number of cuts.
"""

import subprocess
from pathlib import Path
from typing import Callable

from app import DATA_DIR, FFMPEG
from app.ass import build_ass
from app.emoji import emoji_png
from app.media import partial_path
from app.plan import FONT_DIR, Plan

VIDEO_BITRATE = "10M"
JOIN_FADE_S = 0.008   # tiny fade at every audio join so jump cuts do not click
BEEP_HZ = 1000
BEEP_VOLUME = 0.25
FILL_BLUR = 30


def _escape_filter_path(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace(",", "\\,")


def _crop_commands(plan: Plan) -> str:
    """One sendcmd line per output frame, positioning the crop window."""
    max_x = plan.source_width - plan.crop_w
    lines = []
    for n in range(round(plan.duration * plan.fps)):
        t = n / plan.fps
        x = min(max(round(plan.crop_at(t) * plan.source_width - plan.crop_w / 2), 0), max_x)
        lines.append(f"{t:.6f} crop@c x {x};")
    return "\n".join(lines) + "\n"


def _video_chain(plan: Plan, work: Path) -> str:
    first = plan.segments[0].start_frame
    keep = "+".join(f"between(n,{s.start_frame - first},{s.end_frame - first - 1})" for s in plan.segments)
    chain = f"[0:v]select='{keep}',setpts=N/({plan.fps})/TB"
    ass = f"ass=filename={_escape_filter_path(work / 'captions.ass')}:fontsdir={_escape_filter_path(FONT_DIR)}"
    if plan.framing == "fill":
        return (f"{chain},split[bg][fg];"
                f"[bg]scale={plan.width}:{plan.height}:force_original_aspect_ratio=increase,crop={plan.width}:{plan.height},gblur=sigma={FILL_BLUR}[bgb];"
                f"[fg]scale={plan.width}:{plan.height}:force_original_aspect_ratio=decrease[fgf];"
                f"[bgb][fgf]overlay=(W-w)/2:(H-h)/2,{ass}[vb]")
    if plan.crop_w < plan.source_width or plan.crop_h < plan.source_height:
        (work / "crop.cmd").write_text(_crop_commands(plan))
        y = (plan.source_height - plan.crop_h) // 2
        chain += (f",sendcmd=f={_escape_filter_path(work / 'crop.cmd')},"
                  f"crop@c=w={plan.crop_w}:h={plan.crop_h}:x=0:y={y}")
    return f"{chain},scale={plan.width}:{plan.height}:flags=lanczos,setsar=1,{ass}[vb]"


def _emoji_chain(plan: Plan, first_input: int, inputs: dict[str, int]) -> str:
    """Overlay each emoji picture for its time window; `inputs` maps emoji -> ffmpeg input index."""
    label = "vb"
    parts = []
    for n, event in enumerate(plan.emojis):
        y = round(plan.emoji_y - plan.emoji_px / 2)
        parts.append(f"[{label}][{inputs[event.emoji]}:v]overlay=x=(W-w)/2:y={y}:shortest=1:"
                     f"enable='between(t,{event.start:.3f},{event.end:.3f})'[ve{n}]")
        label = f"ve{n}"
    parts.append(f"[{label}]format=yuv420p[v]")
    return ";".join(parts)


def _audio_chain(plan: Plan) -> str:
    first = plan.segments[0].start_frame
    parts = []
    for i, s in enumerate(plan.segments):
        a, b = (s.start_frame - first) / plan.fps, (s.end_frame - first) / plan.fps
        fade = min(JOIN_FADE_S, (b - a) / 4)
        parts.append(f"[0:a]atrim={a:.6f}:{b:.6f},asetpts=PTS-STARTPTS,"
                     f"afade=t=in:d={fade:.4f},afade=t=out:st={b - a - fade:.6f}:d={fade:.4f}[a{i}];")
    joined = "".join(parts) + "".join(f"[a{i}]" for i in range(len(plan.segments)))
    if not plan.beeps:
        return f"{joined}concat=n={len(plan.segments)}:v=0:a=1[a]"
    spans = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in plan.beeps)
    return (f"{joined}concat=n={len(plan.segments)}:v=0:a=1,volume=0:enable='{spans}'[speech];"
            f"aevalsrc='{BEEP_VOLUME}*sin(2*PI*{BEEP_HZ}*t)*({spans})':s=48000:d={plan.duration:.6f}[beep];"
            f"[speech][beep]amix=inputs=2:normalize=0:duration=first[a]")


def render(plan: Plan, source: Path, out: Path, log: Callable[[str], None], frame_offset: int = 0) -> Path:
    """`frame_offset` is the source frame that `source` starts at (0 for the full video, the window start for a mezzanine)."""
    if not plan.segments:
        raise RuntimeError("This clip has no words left to render.")
    work = out.parent
    work.mkdir(parents=True, exist_ok=True)
    (work / "captions.ass").write_text(build_ass(plan), encoding="utf-8")
    first, last = plan.segments[0].start_frame - frame_offset, plan.segments[-1].end_frame - frame_offset
    if first < 0:
        raise RuntimeError("This clip now reaches outside the part of the video that was kept.")
    # Seek half a frame early so the first decoded frame is exactly `first`.
    start, end = max(0.0, (first - 0.5) / plan.fps), (last + 0.5) / plan.fps
    distinct = list(dict.fromkeys(e.emoji for e in plan.emojis))
    inputs = {emoji: index + 1 for index, emoji in enumerate(distinct)}
    images: list[str] = []
    for emoji in distinct:
        images += ["-loop", "1", "-i", str(emoji_png(emoji, plan.emoji_px, DATA_DIR / "emoji"))]
    graph = _video_chain(plan, work) + ";" + _emoji_chain(plan, 1, inputs) + ";" + _audio_chain(plan)
    partial = partial_path(out)
    cmd = [FFMPEG, "-y", "-v", "error", "-ss", f"{start:.6f}", "-to", f"{end:.6f}", "-i", str(source), *images,
           "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
           "-c:v", "h264_videotoolbox", "-b:v", VIDEO_BITRATE, "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
           "-movflags", "+faststart", str(partial)]
    log(f"Rendering {plan.duration:.1f}s at {plan.width}x{plan.height}...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg could not render this clip: {result.stderr.strip()[-600:]}")
    partial.replace(out)
    return out
