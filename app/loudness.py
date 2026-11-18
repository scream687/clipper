"""Loudness as a signal (idea from PeakCut): seconds where the audio jumps well above the video's normal
level are usually laughs, shouting or reactions. They are marked for the AI, and when the AI is
unavailable clips are picked around them instead."""

import re
import statistics
import subprocess
from pathlib import Path

from app import FFMPEG
from app.model import Clip, Video
from app.sentences import split_sentences

Z_LOUD = 2.0          # robust z (median / MAD): quiet pauses do not inflate the spread
MAD_TO_SD = 1.4826
PEAK_WEIGHT = 3       # a YouTube replay peak counts like three loud seconds in the fallback
SILENCE_DB = -60.0
LEVEL_RE = re.compile(r"RMS_level=(-?[\d.]+|-inf)")


def measure(source: Path) -> list[float]:
    """RMS level in dB for every second of audio."""
    result = subprocess.run([FFMPEG, "-v", "error", "-i", str(source), "-vn", "-af",
                             "aresample=8000,asetnsamples=n=8000,astats=metadata=1:reset=1,"
                             "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-", "-f", "null", "-"],
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Could not measure loudness: {result.stderr.strip()[-200:]}")
    return [SILENCE_DB if v == "-inf" else max(float(v), SILENCE_DB) for v in LEVEL_RE.findall(result.stdout)]


def loud_seconds(levels: list[float]) -> list[int]:
    voiced = [v for v in levels if v > SILENCE_DB]
    if len(voiced) < 10:
        return []
    median = statistics.median(voiced)
    spread = statistics.median(abs(v - median) for v in voiced) * MAD_TO_SD
    if spread < 1.0:  # flat or heavily mastered audio: nothing stands out
        return []
    return [i for i, v in enumerate(levels) if (v - median) / spread > Z_LOUD]


def loudness_clips(video: Video, min_s: float, max_s: float, max_clips: int) -> list[Clip]:
    """No-AI fallback: one clip per loud burst or replay peak, whole sentences, strongest first, no overlaps."""
    from app.retention import pick_peaks

    sentences = split_sentences(video.words)
    loud = set(video.loud)
    peaks = {int(p["time"]) for p in pick_peaks(video.heatmap)}
    scored = []
    for i, s in enumerate(sentences):
        span = range(int(s[0].start), int(s[-1].end) + 1)
        hits = sum(1 for t in span if t in loud) + PEAK_WEIGHT * sum(1 for t in span if t in peaks)
        if hits:
            scored.append((hits, i))
    clips: list[Clip] = []
    taken: list[tuple[float, float]] = []
    for hits, i in sorted(scored, reverse=True):
        first = last = i
        while first > 0 and sentences[i][-1].end - sentences[first - 1][0].start <= (min_s + max_s) / 2:
            first -= 1
            if sentences[last][-1].end - sentences[first][0].start >= min_s:
                break
        while last + 1 < len(sentences) and sentences[last + 1][-1].end - sentences[first][0].start <= max_s \
                and sentences[last][-1].end - sentences[first][0].start < min_s:
            last += 1
        start, end = sentences[first][0].start, sentences[last][-1].end
        if any(start < b and end > a for a, b in taken):
            continue
        taken.append((start, end))
        title = " ".join(w.display for w in sentences[i][:8])
        clips.append(Clip(index=len(clips), first_word=sentences[first][0].id, last_word=sentences[last][-1].id,
                          shape=video.shape, title=title[:80], hook=title[:60], score=min(10, 4 + hits),
                          description="Picked by loudness and replays because the AI was unavailable."))
        if len(clips) >= max_clips:
            break
    return [c.model_copy(update={"index": n}) for n, c in enumerate(sorted(clips, key=lambda c: c.first_word))]


def level_speech(source: Path, start: float, duration: float, target: Path, lufs: float = -14.0) -> Path:
    """The speech of `duration` seconds of `source` from `start` as a levelled WAV: a gentle compressor evens the
    voice, then loudnorm lands it at `lufs` with peaks held under -1.5 dBTP (a limiter). Audio preparation, the
    way the preview copy is prepared: the clip itself is still put together by HyperFrames."""
    target.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run([FFMPEG, "-v", "error", "-y", "-ss", f"{max(start, 0):.6f}", "-t", f"{duration:.6f}", "-i", str(source),
                             "-vn", "-af", f"acompressor=threshold=-24dB:ratio=3:attack=10:release=200:makeup=2,"
                             f"loudnorm=I={lufs}:TP=-1.5:LRA=9,aresample=48000", "-ac", "2", "-c:a", "pcm_s16le", str(target)],
                            capture_output=True, text=True)
    if result.returncode != 0 or not target.is_file():
        raise RuntimeError(f"Could not level the speech: {result.stderr.strip()[-300:]}")
    return target


def integrated_lufs(source: Path, start: float, duration: float) -> tuple[float, float] | None:
    """(integrated loudness in LUFS, true peak in dBTP) of `duration` seconds of `source` from `start`; None if it
    cannot be measured."""
    result = subprocess.run([FFMPEG, "-hide_banner", "-ss", f"{max(start, 0):.3f}", "-t", f"{duration:.3f}", "-i", str(source),
                             "-vn", "-af", "ebur128=framelog=quiet:peak=true", "-f", "null", "-"], capture_output=True, text=True)
    level = re.findall(r"^\s*I:\s+(-?[\d.]+) LUFS", result.stderr, re.M)
    peak = re.findall(r"^\s*Peak:\s+(-?[\d.]+|-inf) dBFS", result.stderr, re.M)
    if result.returncode != 0 or not level:
        return None
    return float(level[-1]), float(peak[-1]) if peak and peak[-1] != "-inf" else -60.0
