"""ffprobe and the scrub-friendly preview proxy."""

import json
import subprocess
from fractions import Fraction
from pathlib import Path

from app import FFMPEG, FFPROBE

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
    partial = proxy.with_suffix(".partial.mp4")
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
