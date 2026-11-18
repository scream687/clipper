"""Score Clipper against the Shorts creators cut from their own episodes. Prints a score out of 10.

  uv run python -m scripts.bench <video_id>             score the clips of a video in the library
  uv run python -m scripts.bench --urls URL [URL ...]   eval: pick clips for each episode from its captions,
                                                        comments and replay graph (no download), then score
Add --refresh to re-read the creators' Shorts. Eval data is cached in data/bench/.
"""

import json
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from app import library
from app.ai import ask_ai
from app.benchmark import BENCH_DIR, channel_shorts, place_shorts, score
from app.comments import fetch_comments, keep_for_ai, match_quotes
from app.model import Video, Word
from app.moments import cold_open_end, find_clips
from app.sentences import split_sentences
from app.taste import taste_note
from app.trends import current_note
from app.youtube import download_caption_words, fetch_info, find_orig_caption_code

TOP = 10  # also score the 10 clips the AI rates best: fair across videos that get different clip counts


def _clock(s: float) -> str:
    s = int(s)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _report(title: str, result: dict, top: dict) -> None:
    print(f"\n{title}\n  creator Shorts from it: {result['creator_shorts']} | all {result['clips']} clips found "
          f"{result['found']} -> {result['score']}/10 | best {TOP} found {top['found']} -> {top['score']}/10")
    if result["your_rating"]:
        print(f"  your average rating: {result['your_rating']}/10 over {result['rated']} clips")
    for s in result["missed"]:
        print(f"  missed {_clock(s['start'])}-{_clock(s['end'])}  {s['title'][:80]}")


def library_video(video_id: str, refresh: bool) -> int:
    video = library.load_video(video_id)
    info = fetch_info(video.url)
    shorts = place_shorts(channel_shorts(info["channel_id"], refresh=refresh), video.words)
    if not shorts:
        print("The creator has no Shorts from this episode among their latest, so there is nothing to score.")
        return 1
    clips = library.load_clips(video_id)
    _report(video.title, score(video, clips, shorts), score(video, clips, shorts, TOP))
    return 0


def _episode(url: str) -> Video:
    """Captions, replay graph and comments of an episode, cached in data/bench/<id>/."""
    vid = url.split("v=", 1)[-1].split("&", 1)[0]
    folder = BENCH_DIR / vid
    cached = folder / "video.json"
    if cached.exists():
        return Video.model_validate_json(cached.read_text(encoding="utf-8"))
    info = fetch_info(url)
    raw = download_caption_words(url, find_orig_caption_code(info) or "en-orig", folder / "work")
    words = [Word(id=i, text=w["word"], display=w["word"], start=w["start"], end=w["end"]) for i, w in enumerate(raw)]
    try:
        comments = fetch_comments(url, float(info["duration"]))
    except Exception as error:
        print(f"  no comments for {vid}: {str(error)[:100]}")
        comments = []
    video = Video(id=vid, title=info["title"], channel=info.get("channel") or "", url=url,
                  duration=float(info["duration"]), fps=0, width=0, height=0, words=words,
                  heatmap=list(info.get("heatmap") or []), comments=keep_for_ai(match_quotes(comments, words)),
                  options={"channel_id": info["channel_id"]})
    folder.mkdir(parents=True, exist_ok=True)
    cached.write_text(video.model_dump_json(), encoding="utf-8")
    return video


def evaluate(urls: list[str], refresh: bool) -> int:
    with ThreadPoolExecutor(max_workers=3) as pool:
        videos = list(pool.map(_episode, urls))
    trends, taste = current_note(), taste_note()
    placed = {v.id: place_shorts(channel_shorts(v.options["channel_id"], refresh=refresh), v.words) for v in videos}
    seen = Counter(s["id"] for found in placed.values() for s in found)
    totals = [0, 0, 0]
    for video in videos:
        # a Short found in two episodes is a promo every episode carries, not a moment someone picked
        # ...and one cut from the cold-open teaser is the episode's trailer
        sentences = split_sentences(video.words)
        teaser = cold_open_end(sentences)
        teaser_end = sentences[teaser][-1].end if teaser >= 0 else 0.0
        shorts = [s for s in placed[video.id] if seen[s["id"]] == 1 and s["start"] >= teaser_end]
        if not shorts:
            print(f"\n{video.title}\n  skipped: no creator Shorts from it among their latest")
            continue
        clips = find_clips(video, ask_ai, trends=trends, taste=taste)
        (BENCH_DIR / video.id / "picks.json").write_text(json.dumps([c.model_dump() for c in clips]), encoding="utf-8")
        result, top = score(video, clips, shorts), score(video, clips, shorts, TOP)
        _report(video.title, result, top)
        totals = [totals[0] + len(shorts), totals[1] + result["found"], totals[2] + top["found"]]
    if totals[0]:
        print(f"\nOVERALL: {totals[0]} creator Shorts | all clips found {totals[1]} -> "
              f"{10 * totals[1] / totals[0]:.1f}/10 | best {TOP} found {totals[2]} -> {10 * totals[2] / totals[0]:.1f}/10")
    return 0


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--refresh"]
    refresh = "--refresh" in sys.argv
    if args[:1] == ["--urls"] and len(args) > 1:
        return evaluate(args[1:], refresh)
    if len(args) == 1:
        return library_video(args[0], refresh)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
