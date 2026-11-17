"""Stored data: one video.json per video, one clip.json per clip.

Times in stored data are always SOURCE seconds, and edits point at word ids,
so nothing stored has to move when a clip is trimmed or words are cut.
"""

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

Shape = Literal["9:16", "1:1", "16:9"]
Look = Literal["highlight", "karaoke", "pop", "boxed"]
FramingMode = Literal["crop", "fill"]  # crop = follow keys; fill = whole frame on a blurred background


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
    chapters: list[dict] = []
    created_at: str = ""
    options: dict = {}  # what the user asked for when adding it: max_clips, min_s, max_s, direction


class Clip(BaseModel):
    index: int
    first_word: int
    last_word: int
    cut_word_ids: list[int] = []
    text_fixes: dict[int, str] = {}
    look: Look = "highlight"
    shape: Shape = "9:16"
    framing: FramingMode = "crop"
    auto_keys: list[CropKey] = []
    manual_keys: list[CropKey] = []
    hook: str = ""
    show_hook: bool = True
    show_handle: bool = True
    show_credit: bool = True
    swear_filter: bool = False
    title: str = ""
    description: str = ""
    score: float = 0
    retention: float | None = None
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
