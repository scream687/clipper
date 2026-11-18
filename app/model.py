"""Stored data: one video.json per video, one clip.json per clip.

Times in stored data are always SOURCE seconds, and edits point at word ids,
so nothing stored has to move when a clip is trimmed or words are cut.
"""

import json
import os
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field

Shape = Literal["9:16", "4:5", "1:1", "16:9"]
Look = Literal["highlight", "karaoke", "pop", "build", "line", "boxed"]
FramingMode = Literal["crop", "fill", "split"]  # crop = follow keys; fill = whole frame on the canvas; split = facecam over the video
Background = Literal["blur", "color", "gradient"]
HEX_COLOUR = r"^#[0-9A-Fa-f]{6}$"
UnitFloat = Annotated[float, Field(ge=0, le=1)]


class Word(BaseModel):
    id: int
    text: str        # as transcribed (may be Devanagari)
    display: str     # as shown in captions (Roman for Hindi/Hinglish)
    start: float
    end: float


class CropKey(BaseModel):
    t: float                      # source seconds
    cx: float = Field(ge=0, le=1)  # crop centre, 0 = left edge of the frame, 1 = right edge
    cy: float = Field(default=0.5, ge=0, le=1)
    jump: bool = False             # True = hard cut to this position (speaker change), False = glide
    fit: bool = False              # True = show the whole frame on a blurred fill until the next key (no face to follow)


class Video(BaseModel):
    id: str
    title: str
    channel: str = ""
    url: str = ""
    duration: float
    fps: float
    width: int
    height: int
    language: str = "en"
    shape: Shape = "9:16"
    stage: str = "new"
    error: str | None = None
    words: list[Word] = []
    heatmap: list[dict] = []
    loud: list[int] = []      # seconds where the audio is well above this video's normal level
    comments: list[dict] = []  # top comments: {text, likes, times}; only YouTube links have them
    chat_peaks: list[dict] = []  # where the live chat spiked: {time, chat_at, x, sample}; streams only
    chapters: list[dict] = []
    created_at: str = ""
    options: dict = {}  # what the user asked for when adding it: max_clips, min_s, max_s, direction
    content: str = ""  # talk | gameplay | show | vlog | stream; "" = not known (framing then uses faces alone)


class Clip(BaseModel):
    index: int
    first_word: int
    last_word: int
    cut_word_ids: list[int] = []
    text_fixes: dict[int, str] = {}
    look: Look = "highlight"
    shape: Shape = "9:16"
    framing: FramingMode = "crop"
    # The canvas behind the whole frame (and behind faceless wide shots in a cropped clip).
    background: Background = "blur"
    bg_color: str = Field(default="#111111", pattern=HEX_COLOUR)
    bg_color2: str = Field(default="#3A2A10", pattern=HEX_COLOUR)  # bottom colour of the gradient
    padding: int = Field(default=0, ge=0, le=20)   # % of the output width kept clear around the picture
    radius: int = Field(default=0, ge=0, le=80)    # rounded corners, px at a 1080 short side
    # Split: facecam on top, the video below. Boxes are [x, y, w, h] as shares of the source frame.
    cam_box: list[float] | None = None   # found facecam; [] = looked and there is none; None = not looked yet
    split_cam: list[UnitFloat] | None = Field(default=None, min_length=4, max_length=4)  # the user's facecam crop
    split_bottom: float | None = Field(default=None, ge=0, le=1)  # centre of the video crop below; None = middle
    auto_keys: list[CropKey] = []
    manual_keys: list[CropKey] = []
    hook: str = ""
    show_hook: bool = True
    show_handle: bool = True
    show_credit: bool = True
    swear_filter: bool = False
    emoji_words: dict[int, str] = {}  # word id -> emoji shown above the captions from that word
    show_emoji: bool = True
    music: str = ""                                   # a track in data/music/; "" = none
    music_volume: int = Field(default=15, ge=0, le=100)  # % under the speech
    music_peak: int = Field(default=15, ge=0, le=100)    # % it rises to over the last riser_s seconds
    riser_s: int = Field(default=0, ge=0, le=15)
    show_logo: bool = False                           # data/logo.png in a corner
    logo_corner: Literal["tl", "tr", "bl", "br"] = "tr"
    logo_size: int = Field(default=14, ge=6, le=30)   # % of the output width
    show_follow: bool = False                         # "Follow @handle for more" over the last seconds
    template: Literal["none", "tweet"] = "none"       # tweet: the clip under an X post whose text is the hook
    tweet_theme: Literal["light", "dark"] = "light"
    title: str = ""
    description: str = ""
    hashtags: str = ""  # "#tag #tag", posted with the description
    score: float = 0
    source: str = ""  # "" = found here; "opusclip" = imported from OpusClip
    retention: float | None = None
    mentions: int | None = None  # comments whose timestamps point into this clip
    rating: int | None = Field(default=None, ge=1, le=10)  # the user's own score; teaches later picks their taste
    tighten: bool = True    # pacing: fillers cut, long pauses shortened
    punch_in: bool = True   # pacing: zoom in on alternate shots so the picture keeps changing
    sfx: bool = True        # sound effects on punch-ins, emojis and the punchline
    sfx_safe_only: bool = False  # only built-in and uploaded sounds (myinstants memes can trigger copyright claims)
    meme_word: int | None = None  # the punchline word the AI picked for a meme sound
    emphasis_words: list[int] = []  # key words the AI picked: accent colour and a bounce in the captions
    show_progress: bool = True      # a thin progress bar along the bottom
    broll: list[dict] = []          # inserts the AI picked: first_word, last_word, kind (stock|card), text, sub, query
    show_broll: bool = True
    status: Literal["new", "kept", "discarded"] = "new"
    framed: bool = False  # automatic framing has run for this clip
    mezz_start_frame: int | None = None  # source frames kept in clips/<n>/mezz.mp4 once the full video is cleaned up
    mezz_end_frame: int | None = None


def write_json(path: Path, model: BaseModel) -> None:
    """Write atomically, so a crash mid-write never leaves half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(model.model_dump_json(indent=1), encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path, model: type[BaseModel]):
    return model.model_validate(json.loads(path.read_text(encoding="utf-8")))
