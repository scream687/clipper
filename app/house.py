"""The house look: the user's saved finish (captions, branding, music, canvas) that every new clip starts with,
and the video's content type, which decides how clips are framed."""

import json
from typing import Callable

from app import SETTINGS_FILE
from app.model import Clip, Video

# Clip fields that make up a look. Framing, keys and words are per clip and never copied.
LOOK_FIELDS = ("look", "background", "bg_color", "bg_color2", "padding", "radius", "show_hook", "show_handle",
               "show_credit", "swear_filter", "show_emoji", "music", "music_volume", "music_peak", "riser_s",
               "show_logo", "logo_corner", "logo_size", "show_follow", "template", "tweet_theme", "tighten", "punch_in",
               "sfx", "sfx_safe_only", "show_progress", "show_broll")

CONTENT_TYPES = ("talk", "gameplay", "show", "vlog", "stream")
CONTENT_SCHEMA = {"type": "object", "properties": {"content": {"type": "string", "enum": list(CONTENT_TYPES)}},
                  "required": ["content"], "additionalProperties": False}
SAMPLE_WORDS = 400  # transcript words shown to the AI to tell the kind of video apart


def look_of(clip: Clip) -> dict:
    return {k: getattr(clip, k) for k in LOOK_FIELDS}


def saved_look() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8")).get("house_look") or {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def with_look(clip: Clip, look: dict) -> Clip:
    """`clip` wearing `look`; unknown or invalid values are skipped, so an old saved look never breaks a clip."""
    known = {k: v for k, v in look.items() if k in LOOK_FIELDS}
    try:
        return Clip.model_validate({**clip.model_dump(), **known})
    except ValueError:
        return clip


def classify(video: Video, ask: Callable[[str, dict, str], dict]) -> str:
    """'talk' (podcast, interview, lecture), 'gameplay', 'show' (stage, comedy, reality), 'vlog' or 'stream'."""
    words = video.words
    middle = max(0, len(words) // 2 - SAMPLE_WORDS // 2)
    sample = " ".join(w.display for w in words[middle:middle + SAMPLE_WORDS])
    prompt = (f"Title: {video.title}\nChannel: {video.channel}\nLength: {round(video.duration / 60)} min\n"
              f"Transcript sample: {sample}\n\nWhat kind of video is this? talk = podcast, interview, lecture or one person "
              "talking to camera; gameplay = a recorded video game (a facecam may be in a corner); show = a stage show, "
              "comedy, reality or anything with an audience and many people; vlog = a handheld camera following someone "
              "around; stream = a recorded livestream (gaming or just chatting).")
    answer = ask(prompt, CONTENT_SCHEMA, "You sort videos for a video editor. You return only the requested JSON.")
    return answer["content"] if answer.get("content") in CONTENT_TYPES else ""
