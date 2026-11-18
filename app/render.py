"""Render a plan to MP4 with HyperFrames: app/compose.py writes the whole clip as one composition (the cut and framed
source, Remotion's inserts, captions and graphics, every sound) and HyperFrames renders it. ffmpeg builds nothing
here; it only measured the speech level beforehand (analysis)."""

import shutil
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Callable

from app import compose, inserts as cutaways
from app.loudness import integrated_lufs, level_speech
from app.media import partial_path
from app.plan import Plan

HYPERFRAMES = "hyperframes@0.9.65"
LOUDNESS_LUFS = -14          # where Reels and Shorts play
GAIN_RANGE = (0.3, 2.5)      # never boost speech more than ~8 dB
PEAK_CEILING_DB = -1.0       # HyperFrames has no limiter: the boost stops where the loudest peak would reach this
TIMEOUT_S = 3600


def _rate(fps: float) -> str:
    rate = Fraction(fps).limit_denominator(1001)
    return str(rate.numerator) if rate.denominator == 1 else f"{rate.numerator}/{rate.denominator}"


def voice_gain(plan: Plan, source: Path, frame_offset: int) -> float:
    """The speech volume that lands the clip at LOUDNESS_LUFS, measured over the source span the clip covers."""
    first, last = plan.segments[0].start_frame - frame_offset, plan.segments[-1].end_frame - frame_offset
    measured = integrated_lufs(source, first / plan.fps, (last - first) / plan.fps)
    if measured is None:
        return 1.0
    level, peak = measured
    wanted_db = min(LOUDNESS_LUFS - level, PEAK_CEILING_DB - peak)  # loud enough, never clipping
    return min(max(10 ** (wanted_db / 20), GAIN_RANGE[0]), GAIN_RANGE[1])


def render(plan: Plan, source: Path, out: Path, log: Callable[[str], None], frame_offset: int = 0,
           out_fps: float | None = None) -> Path:
    """`frame_offset` is the source frame that `source` starts at (0 for the full video, the window start for a mezzanine)."""
    if not plan.segments:
        raise RuntimeError("This clip has no words left to render.")
    if plan.segments[0].start_frame - frame_offset < 0:
        raise RuntimeError("This clip now reaches outside the part of the video that was kept.")
    if shutil.which("npx") is None:
        raise RuntimeError("Exports are rendered by HyperFrames, which needs Node.js (npx). Install Node 22 or newer.")
    if plan.music:
        from app import assets
        try:
            assets.music_path(plan.music["file"])
        except KeyError:
            raise RuntimeError(f"The music track {plan.music['file']} is no longer in the library. Pick another one.") from None
    work = out.parent
    work.mkdir(parents=True, exist_ok=True)
    cuts = cutaways.render_all(plan, source, frame_offset / plan.fps, work / "inserts", log)
    tweet = None
    if plan.tweet:
        from app.template import tweet_header
        tweet = work / "tweet.png"
        tweet_header(plan).save(tweet)
    first, last = plan.segments[0].start_frame - frame_offset, plan.segments[-1].end_frame - frame_offset
    try:  # levelled speech (compressor + limiter) so quiet sources reach -14 LUFS without clipping
        voice = level_speech(source, first / plan.fps, (last - first) / plan.fps, work / "voice.wav", LOUDNESS_LUFS)
    except RuntimeError as error:
        log(f"{error} Using the original speech with a safe gain.")
        voice = None
    project = compose.write(plan, source, frame_offset, work, cuts, voice_gain(plan, source, frame_offset), tweet, voice)
    partial = partial_path(out)
    log(f"Rendering {plan.duration:.1f}s at {plan.width}x{plan.height} with HyperFrames...")
    result = subprocess.run(["npx", "--yes", HYPERFRAMES, "render", str(project), "--format", "mp4",
                             "--fps", _rate(out_fps or plan.fps), "--quality", "delivery", "--output", str(partial), "--quiet"],
                            capture_output=True, text=True, timeout=TIMEOUT_S)
    if result.returncode != 0 or not partial.is_file():
        partial.unlink(missing_ok=True)
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"HyperFrames could not render this clip: {detail[-600:]}")
    partial.replace(out)
    return out
