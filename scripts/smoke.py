"""End-to-end check on a real YouTube video: analyse (download, proxy, transcript, Claude picks) and
render the best clip in every caption look. Exits non-zero if any export disagrees with its plan.

Run:  uv run python -m scripts.smoke [youtube_url]
"""

import json
import subprocess
import sys
import time

from app import FFMPEG, FFPROBE, library
from app.pipeline import analyse, create_from_youtube
from app.plan import LOOKS, build_plan
from app.render import render

DEFAULT_URL = "https://www.youtube.com/watch?v=arj7oStGLkU"


def frames_and_audio(path) -> tuple[int, float]:
    out = json.loads(subprocess.run([FFPROBE, "-v", "error", "-count_frames", "-show_entries",
                                     "stream=codec_type,nb_read_frames,duration", "-of", "json", str(path)],
                                    capture_output=True, text=True, check=True).stdout)["streams"]
    v = next(s for s in out if s["codec_type"] == "video"); a = next(s for s in out if s["codec_type"] == "audio")
    return int(v["nb_read_frames"]), float(a["duration"])


def main() -> int:
    target = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    started = time.time()
    if target.startswith("http"):
        video = create_from_youtube(target, {"max_clips": 10, "min_s": 20, "max_s": 60}, "9:16")
    else:  # an existing video id: resume its analysis instead of downloading again
        video = library.load_video(target)
    video = analyse(video.id, log=lambda m: print(f"  {m}"))
    clips = library.load_clips(video.id)
    print(f"analysed in {time.time() - started:.0f}s: {len(video.words)} words, {len(clips)} clips")
    for c in clips:
        print(f"  #{c.index} score {c.score} replay {c.retention} | {c.title} | hook: {c.hook}")

    failures = []
    best = clips[0]
    cut = [best.first_word + 3, best.first_word + 4]  # also prove jump cuts work end to end
    for look in LOOKS:
        clip = best.model_copy(update={"look": look, "cut_word_ids": cut})
        plan = build_plan(video, clip, handle="@clipper")
        out = library.clip_dir(video.id, clip.index) / f"smoke_{look}.mp4"
        t0 = time.time()
        render(plan, library.video_dir(video.id) / "source.mp4", out, log=lambda m: None)
        frames, audio = frames_and_audio(out)
        expected = round(plan.duration * plan.fps)
        print(f"{look}: {plan.duration:.2f}s rendered in {time.time() - t0:.1f}s, frames {frames}/{expected}, audio {audio:.3f}s")
        if frames != expected or abs(audio - plan.duration) > 0.05:
            failures.append(f"{look}: frames {frames} vs {expected}, audio {audio:.3f} vs {plan.duration:.3f}")
        subprocess.run([FFMPEG, "-y", "-v", "error", "-ss", "1.5", "-i", str(out), "-frames:v", "1", "-vf", "scale=360:-1",
                        str(out.with_suffix(".png"))], check=True)
    for failure in failures:
        print("FAIL:", failure)
    print("SMOKE", "FAILED" if failures else "PASSED", "- video", video.id)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
