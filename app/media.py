"""ffprobe, the scrub-friendly preview proxy, and kept clip windows."""

import json
import os
import subprocess
from fractions import Fraction
from pathlib import Path

from app import FFMPEG, FFPROBE

PARTIAL_GLOB = "*.partial-*.mp4"


def partial_path(out: Path) -> Path:
    """A temporary name unique to this server process. An ffmpeg orphaned by a killed server keeps writing
    its own file instead of corrupting the one a restarted server is writing."""
    return out.with_name(f"{out.stem}.partial-{os.getpid()}{out.suffix}")


def remove_stale_partials(folder: Path) -> None:
    for stale in folder.rglob(PARTIAL_GLOB):
        if not stale.name.endswith(f".partial-{os.getpid()}.mp4"):
            stale.unlink(missing_ok=True)

PROXY_HEIGHT = 720
PROXY_GOP = 12  # a keyframe every 12 frames keeps seeks for jump cuts fast


def probe(path: Path) -> dict:
    out = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height,avg_frame_rate,r_frame_rate:format=duration", "-of", "json", str(path)],
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"Could not read the video file: {out.stderr.strip()[-300:]}")
    info = json.loads(out.stdout)
    stream = info["streams"][0]
    rate = stream.get("avg_frame_rate") or stream["r_frame_rate"]
    if rate in ("0/0", ""):
        rate = stream["r_frame_rate"]
    return {"width": int(stream["width"]), "height": int(stream["height"]),
            "fps": float(Fraction(rate)), "duration": float(info["format"]["duration"])}


def make_proxy(source: Path, proxy: Path) -> Path:
    partial = partial_path(proxy)
    result = subprocess.run([FFMPEG, "-y", "-v", "error", "-i", str(source), "-vf", f"scale=-2:{PROXY_HEIGHT}",
                             "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-g", str(PROXY_GOP),
                             "-keyint_min", str(PROXY_GOP), "-sc_threshold", "0", "-bf", "0",
                             "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(partial)],
                            capture_output=True, text=True)
    if result.returncode != 0:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"Could not build the preview copy: {result.stderr.strip()[-300:]}")
    partial.replace(proxy)
    return proxy


def make_mezzanine(source: Path, out: Path, start_frame: int, end_frame: int, fps: float) -> Path:
    """Keep source frames [start_frame, end_frame) as a small, seek-friendly file, frame for frame."""
    partial = partial_path(out)
    result = subprocess.run([FFMPEG, "-y", "-v", "error", "-ss", f"{max(0.0, (start_frame - 0.5) / fps):.6f}", "-i", str(source),
                             "-frames:v", str(end_frame - start_frame), "-t", f"{(end_frame - start_frame) / fps:.6f}",
                             "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-g", str(PROXY_GOP),
                             "-keyint_min", str(PROXY_GOP), "-sc_threshold", "0", "-bf", "0",
                             "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(partial)],
                            capture_output=True, text=True)
    if result.returncode != 0:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"Could not keep the clip's part of the video: {result.stderr.strip()[-300:]}")
    partial.replace(out)
    return out
