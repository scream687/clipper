"""Score how close the clips are to "postable with zero edits", on the rubric in the plan.

For each video id: its best clips are rebuilt the way new clips are made now (content type, framing, house look,
pacing, the AI's edit picks), in memory only (the library is not changed), rendered to OUT, and checked.

Run:  uv run python -m scripts.score OUT VIDEO_ID [VIDEO_ID ...] [--clips 3]
Prints a scorecard per clip and per video, and writes OUT/<video>/<clip>/sheet.png (6 frames) to look at.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from unittest import mock

from app import FFMPEG, FFPROBE, library, pipeline
from app.ai import AIError, AIUnavailable, ask_ai
from app.edits import decorate
from app.framing import sample_faces
from app.house import classify, saved_look, with_look
from app.pacing import FILLERS, tighten
from app.plan import build_plan
from app.render import render

GAME_LIKE = ("gameplay", "stream")
MAX_STILL_S = 6.0      # the picture must change (cut, punch-in, insert) at least this often
MAX_GAP_S = 0.45       # no pause between kept words longer than this
LUFS_RANGE = (-15.0, -13.0)


def probe(path: Path) -> tuple[int, float]:
    out = json.loads(subprocess.run([FFPROBE, "-v", "error", "-count_frames", "-show_entries",
                                     "stream=codec_type,nb_read_frames,duration", "-of", "json", str(path)],
                                    capture_output=True, text=True, check=True).stdout)["streams"]
    v = next(s for s in out if s["codec_type"] == "video"); a = next(s for s in out if s["codec_type"] == "audio")
    return int(v["nb_read_frames"]), float(a["duration"])


def loudness(path: Path) -> float:
    err = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path), "-af", "ebur128=framelog=quiet", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    line = next(l for l in reversed(err.splitlines()) if l.strip().startswith("I:"))
    return float(line.split()[1])


def sheet(path: Path, duration: float, target: Path) -> None:
    times = [duration * k / 7 for k in range(1, 7)]
    picks = "+".join(f"eq(n\\,{round(t * 25)})" for t in times)
    subprocess.run([FFMPEG, "-v", "error", "-y", "-i", str(path), "-vf", f"fps=25,select='{picks}',scale=240:-2,tile=6x1",
                    "-frames:v", "1", str(target)], check=True)


def longest_still(plan) -> float:
    """Longest stretch with no cut, punch-in flip or insert edge (output seconds)."""
    marks = {0.0, plan.duration}
    t = 0.0
    for s in plan.segments[:-1]:
        t += (s.end_frame - s.start_frame) / plan.fps
        marks.add(round(t, 3))
    for a, b in plan.zooms:
        marks |= {a, b}
    for i in plan.inserts:
        marks |= {i.start, i.end}
    ordered = sorted(marks)
    return max(b - a for a, b in zip(ordered, ordered[1:]))


def rebuild(video, clips):
    """The clips as the current pipeline makes them, without saving anything."""
    look = saved_look()
    with mock.patch.object(library, "save_clip", lambda *a, **k: None):
        framed = [pipeline.frame_clip(video, c.model_copy(update={"framed": False, "manual_keys": []})) for c in clips]
    return decorate(video, [tighten(video, with_look(c, look)) for c in framed], ask_ai, log=lambda m: None)


def check(video, clip, out_dir: Path) -> dict:
    plan = build_plan(video, clip, handle="@sfxme")
    source, offset = library.media_for(video.id, clip)
    out = out_dir / "export.mp4"
    render(plan, source, out, log=lambda m: None, frame_offset=offset)
    frames, audio = probe(out)
    lufs = loudness(out)
    sheet(out, plan.duration, out_dir / "sheet.png")
    words = plan.words
    gaps = [b.start - a.end for a, b in zip(words, words[1:])]
    fillers = [w.text for w in words if w.text.lower().strip(",.?!") in FILLERS]
    still = longest_still(plan)
    game = video.content in GAME_LIKE
    framing_ok = plan.framing in ("fill", "split") if video.content == "gameplay" else True
    face_share = None
    if plan.framing == "crop":
        samples, _ = sample_faces(out, 0, plan.duration)
        in_frame = [x for _, x in samples if x is not None and 0.12 <= x <= 0.88]
        face_share = len(in_frame) / max(len(samples), 1)
        framing_ok = face_share >= 0.6 or bool(plan.fit_spans)
    checks = {
        "exact_length": frames == round(plan.duration * plan.fps) and abs(audio - plan.duration) < 0.06,
        "loudness": LUFS_RANGE[0] <= lufs <= LUFS_RANGE[1],
        "framing": framing_ok,
        "pacing_gaps": max(gaps, default=0) <= MAX_GAP_S or not clip.tighten,
        "no_fillers": not fillers,
        "keeps_moving": game or still <= MAX_STILL_S,
        "text_layer": (out_dir / "captions.ass").is_file(),
        "emphasis": len(plan.emphasis) >= 2,
        "visuals": video.content not in ("talk", "vlog") or bool(plan.inserts),  # B-roll over a comedy set or a game is wrong
        "sound": bool(plan.sfx),
        "polish": bool(plan.hook) and plan.progress,
    }
    weights = {"exact_length": 0.5, "loudness": 1.0, "framing": 1.5, "pacing_gaps": 1.0, "no_fillers": 0.5,
               "keeps_moving": 1.0, "text_layer": 1.0, "emphasis": 0.5, "visuals": 1.0, "sound": 0.5, "polish": 0.5}
    auto = sum(w for k, w in weights.items() if checks[k]) / sum(weights.values()) * 9  # picks (1 point) are judged by eye
    hard = ("exact_length", "loudness", "framing", "pacing_gaps", "text_layer")
    return {"clip": clip.index, "title": clip.title, "framing": plan.framing, "duration": round(plan.duration, 1),
            "lufs": lufs, "longest_still_s": round(still, 1), "max_gap_s": round(max(gaps, default=0), 2),
            "face_share": None if face_share is None else round(face_share, 2), "inserts": len(plan.inserts),
            "sfx": len(plan.sfx), "emphasis": len(plan.emphasis), "checks": checks, "auto_score_of_9": round(auto, 2),
            "postable": all(checks[k] for k in hard)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("out")
    parser.add_argument("videos", nargs="+")
    parser.add_argument("--clips", type=int, default=3)
    args = parser.parse_args()
    out_root = Path(args.out)
    report = []
    for video_id in args.videos:
        try:
            video = library.load_video(video_id)
        except KeyError:
            print(f"{video_id}: not in the library, skipped")
            continue
        if not video.content:
            try:
                video = video.model_copy(update={"content": classify(video, ask_ai)})
            except (AIUnavailable, AIError) as error:
                print(f"{video_id}: could not classify ({error})")
        clips = sorted(library.load_clips(video_id), key=lambda c: -c.score)[:args.clips]
        print(f"\n== {video.title[:70]} ({video.content or 'unknown'}) ==", flush=True)
        for clip in rebuild(video, clips):
            try:
                row = check(video, clip, out_root / video_id / f"{clip.index:03d}")
            except Exception as error:  # one broken clip is a finding, not the end of the run
                row = {"clip": clip.index, "title": clip.title, "error": str(error)[:300], "postable": False, "auto_score_of_9": 0}
            report.append({"video": video_id, "content": video.content, **row})
            print(json.dumps(report[-1], ensure_ascii=False), flush=True)
    postable = sum(r["postable"] for r in report) / max(len(report), 1)
    mean = sum(r["auto_score_of_9"] for r in report) / max(len(report), 1)
    (out_root / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False))
    print(f"\nclips: {len(report)}  postable: {postable:.0%}  automatic score: {mean:.2f}/9 (+ picks, judged by eye)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
