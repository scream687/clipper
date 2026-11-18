"""The user's ratings of past clips, turned into examples of their taste for the next pick."""

from app import library

LIKED, DISLIKED = 8, 4      # ratings at or above / at or below these count
MAX_LIKED, MAX_DISLIKED = 6, 4
SNIPPET_WORDS = 35


def _example(video, clip) -> str:
    words = video.words[clip.first_word:clip.last_word + 1][:SNIPPET_WORDS]
    return f"- rated {clip.rating}/10: \"{clip.title}\" (hook: {clip.hook}) {' '.join(w.display for w in words)}…"


def taste_note(skip_video_id: str = "") -> str:
    """Up to 6 liked and 4 disliked clips from other videos, newest videos first. Empty when nothing is rated."""
    liked, disliked = [], []
    for summary in library.list_videos():
        if summary.id == skip_video_id or (len(liked) >= MAX_LIKED and len(disliked) >= MAX_DISLIKED):
            continue
        rated = [c for c in library.load_clips(summary.id) if c.rating]
        for clip in rated:
            if clip.rating >= LIKED and len(liked) < MAX_LIKED:
                liked.append(_example(summary, clip))
            elif clip.rating <= DISLIKED and len(disliked) < MAX_DISLIKED:
                disliked.append(_example(summary, clip))
    parts = (["Rated high:"] + liked if liked else []) + (["Rated low:"] + disliked if disliked else [])
    return "\n".join(parts)
