"""End-to-end render check: one hand-written highlight, three reframe modes, no AI key.

Run:   uv run python -m scripts.smoke [youtube_url]
Exits non-zero if any clip is missing or has the wrong shape. Also writes a few
still frames to data/smoke/frames/ so captions, hook text and framing can be eyeballed.
"""

import json
import shutil
import subprocess
import sys

from app import DATA_DIR, FFMPEG, FFPROBE, cookies_path
from yt_short_clipper_core.clip_processor import (
    DEFAULT_LEAD_IN,
    DEFAULT_TAIL_OUT,
    process_selected_highlights,
)
from yt_short_clipper_core.subtitle_downloader import download_caption_words

DEFAULT_URL = "https://www.youtube.com/watch?v=arj7oStGLkU"
START, END, RANGE_SECONDS = "00:01:00,000", "00:01:20,000", 20.0
EXPECTED_SECONDS = RANGE_SECONDS + DEFAULT_LEAD_IN + DEFAULT_TAIL_OUT
DURATION_TOLERANCE = 1.0
OUTPUT_SIZE = (1080, 1920)
MODES = [("face", "black"), ("centered", "black"), ("centered", "blur")]
FRAME_TIMES = (2.0, 12.0)  # one inside the hook window, one after it


def probe(path: str) -> dict:
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries",
         "stream=codec_type,codec_name,width,height:format=duration", "-of", "json", path],
        capture_output=True, text=True, check=True,
    ).stdout
    info = json.loads(out)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    return {
        "size": (video["width"], video["height"]),
        "codec": video["codec_name"],
        "has_audio": any(s["codec_type"] == "audio" for s in info["streams"]),
        "duration": float(info["format"]["duration"]),
    }


def save_frame(clip: str, seconds: float, out_png: str) -> None:
    subprocess.run(
        [FFMPEG, "-y", "-v", "error", "-ss", str(seconds), "-i", clip,
         "-frames:v", "1", "-vf", "scale=540:-1", out_png],
        check=True,
    )


def main() -> int:
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    session = DATA_DIR / "smoke"
    shutil.rmtree(session, ignore_errors=True)
    frames = session / "frames"
    frames.mkdir(parents=True)

    words = download_caption_words(url, session, cookies_path(), log=print)
    failures = [] if words else ["no word-timed caption track for this video"]

    for index, (mode, background) in enumerate(MODES):
        label = mode if mode == "face" else f"{mode}-{background}"
        result = process_selected_highlights(
            url=url,
            highlights=[{
                "start_time": START, "end_time": END,
                "title": f"Smoke {label}", "description": "Smoke test clip",
                "hook_text": "This is the hook text for the smoke test",
                "_highlight_index": index,
            }],
            session_dir=str(session),
            options={"reframeMode": mode, "centeredBackground": background,
                     "addCaptions": True, "addHook": True, "addCreditWatermark": True},
            ai={"hook_style": {"duration_seconds": 5.0},
                "credit_watermark": {"text": "Credit: smoke test"}},
            log=lambda message: print(f"  [{label}] {message}"),
        )
        clip = result["results"][0]["output_path"]
        info = probe(clip)
        print(f"{label}: {info}")
        if info["size"] != OUTPUT_SIZE:
            failures.append(f"{label}: size {info['size']}, expected {OUTPUT_SIZE}")
        if not info["has_audio"]:
            failures.append(f"{label}: no audio stream")
        if abs(info["duration"] - EXPECTED_SECONDS) > DURATION_TOLERANCE:
            failures.append(f"{label}: {info['duration']:.1f}s, expected about {EXPECTED_SECONDS:.1f}s")
        for seconds in FRAME_TIMES:
            save_frame(clip, seconds, str(frames / f"{label}_{int(seconds):02d}s.png"))

    for failure in failures:
        print("FAIL:", failure)
    print("SMOKE", "FAILED" if failures else "PASSED", f"- frames in {frames}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
