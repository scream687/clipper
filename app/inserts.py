"""Full-frame cutaways rendered by Remotion (motion/src/Insert.tsx): Pexels B-roll with key words, or a number/name
card over the speaker's own frame. Each is cached by its content, so re-exports reuse it."""

import hashlib
import json
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from app import FFMPEG, ROOT, SETTINGS_FILE, pexels
from app.plan import FONTS, Insert, Plan

MOTION = ROOT / "motion"
TIMEOUT_S = 600
DESIGN_VERSION = 3  # bump when motion/src/Insert.tsx changes, so cached inserts are rendered again
PARALLEL = 3


def available() -> bool:
    return (MOTION / "node_modules" / ".bin" / "remotion").exists()


def _pexels_key() -> str:
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8")).get("pexels_key", "")
    except (FileNotFoundError, json.JSONDecodeError):
        return ""


def _still(source: Path, at: float, target: Path) -> None:
    subprocess.run([FFMPEG, "-v", "error", "-y", "-ss", f"{max(at, 0):.3f}", "-i", str(source), "-frames:v", "1",
                    "-q:v", "3", str(target)], check=True, capture_output=True)


def render_one(plan: Plan, insert: Insert, source: Path, source_start_s: float, folder: Path, key: str,
               log: Callable[[str], None]) -> Path:
    """Render one insert to an MP4 the size and rate of the clip. `source_start_s`: the source second `source` starts at."""
    media, media_type, kind = None, "image", insert.kind
    if kind == "stock":
        try:
            found = pexels.fetch(insert.query or insert.text, key)
        except (OSError, ValueError) as error:
            log(f"Pexels could not give B-roll for “{insert.query}” ({str(error)[:80]}); using a card")
            found = None
        if found:
            media, media_type = found
        else:
            kind = "card"  # no key or no footage: the words as a card instead
    frames = max(1, round((insert.end - insert.start) * plan.fps))
    props = {"kind": kind, "text": insert.text, "sub": insert.sub, "mediaType": media_type, "accent": plan.accent,
             "fps": plan.fps, "frames": frames, "width": plan.width, "height": plan.height,
             "media_id": str(media) if media else f"still@{insert.src_t:.3f}", "design": DESIGN_VERSION}
    digest = hashlib.sha1(json.dumps(props, sort_keys=True).encode()).hexdigest()[:16]
    out = folder / f"insert-{digest}.mp4"
    if out.is_file():
        return out
    public = folder / f"public-{digest}"
    public.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FONTS["Arial Black"], public / "arial-black.ttf")  # the Mac's own font, never kept in the repo
    if media:
        name = f"media{media.suffix}"
        shutil.copyfile(media, public / name)
    else:
        name = "still.jpg"
        _still(source, insert.src_t - source_start_s, public / name)
    props = {k: v for k, v in props.items() if k not in ("media_id", "design")} | {"media": name}
    (public / "props.json").write_text(json.dumps(props), encoding="utf-8")
    partial = out.with_suffix(".partial.mp4")
    result = subprocess.run(["npx", "remotion", "render", "src/index.ts", "Insert", str(partial),
                             f"--props={public / 'props.json'}", f"--public-dir={public}", "--log=error"],
                            cwd=MOTION, capture_output=True, text=True, timeout=TIMEOUT_S)
    shutil.rmtree(public, ignore_errors=True)
    if result.returncode != 0 or not partial.is_file():
        raise RuntimeError(f"Remotion could not render the insert “{insert.text}”: {(result.stderr or result.stdout).strip()[-300:]}")
    partial.replace(out)
    return out


def render_all(plan: Plan, source: Path, source_start_s: float, folder: Path,
               log: Callable[[str], None]) -> list[tuple[Insert, Path]]:
    """Every insert that rendered; one that fails is left out (the speaker stays on screen there) and logged."""
    if not plan.inserts or not available():
        return []
    folder.mkdir(parents=True, exist_ok=True)
    key = _pexels_key()
    log(f"Rendering {len(plan.inserts)} B-roll insert{'s' if len(plan.inserts) > 1 else ''}")

    def one(insert: Insert):
        try:
            return insert, render_one(plan, insert, source, source_start_s, folder, key, log)
        except (RuntimeError, OSError, subprocess.SubprocessError) as error:
            log(f"{error} Skipping that insert.")
            return None

    with ThreadPoolExecutor(PARALLEL) as pool:
        return [r for r in pool.map(one, plan.inserts) if r]
