"""Sound effects: a few built-in sounds made with ffmpeg (no licence questions) plus the user's own, imported
from a myinstants.com link or uploaded. Each sound has a role; the plan places roles, the library picks files."""

import json
import re
import subprocess
import urllib.request
from pathlib import Path
from typing import BinaryIO

from app import DATA_DIR, FFMPEG
from app.assets import _duration

SFX_DIR = DATA_DIR / "sfx"
INDEX = SFX_DIR / "sounds.json"
ROLES = ("whoosh", "pop", "impact", "riser", "meme")
NAME = re.compile(r"[A-Za-z0-9_-]{1,80}\.(mp3|m4a|wav)")
MAX_BYTES = 5 * 1024 * 1024
MAX_SECONDS = 8.0
MYINSTANTS = "https://www.myinstants.com"
SOUND_LINK = re.compile(r"/media/sounds/[A-Za-z0-9_.%-]+\.mp3")
PAGE = re.compile(r"^https://(www\.)?myinstants\.com/[A-Za-z0-9_/%.-]*$")

# Built-in sounds: (role, ffmpeg lavfi source). Short, quiet-ending, made fresh on first use.
BUILT_IN = {
    "whoosh.wav": ("whoosh", "anoisesrc=d=0.45:c=pink:a=0.6,highpass=f=300,lowpass=f=5000,"
                             "afade=t=in:d=0.3:curve=exp,afade=t=out:st=0.3:d=0.15,volume=1.4"),
    "pop.wav": ("pop", "sine=f=880:d=0.09,afade=t=out:st=0.02:d=0.07,volume=0.9"),
    "impact.wav": ("impact", "sine=f=55:d=0.6,afade=t=out:st=0.05:d=0.55,volume=2.2"),
    "riser.wav": ("riser", "anoisesrc=d=1.5:c=white:a=0.3,bandpass=f=2000:w=1500,afade=t=in:d=1.4:curve=exp,"
                           "afade=t=out:st=1.4:d=0.1"),
}


def _load() -> list[dict]:
    try:
        return json.loads(INDEX.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save(sounds: list[dict]) -> None:
    SFX_DIR.mkdir(parents=True, exist_ok=True)
    INDEX.write_text(json.dumps(sounds, indent=1), encoding="utf-8")


def ensure_built_in() -> None:
    SFX_DIR.mkdir(parents=True, exist_ok=True)
    sounds = _load()
    for name, (role, source) in BUILT_IN.items():
        if not (SFX_DIR / name).is_file():
            subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", source, "-ar", "48000", "-ac", "2",
                            str(SFX_DIR / name)], check=True)
        if not any(s["name"] == name for s in sounds):
            sounds.append({"name": name, "role": role, "safe": True, "built_in": True})
    _save(sounds)


def list_sounds() -> list[dict]:
    ensure_built_in()
    return [s for s in _load() if (SFX_DIR / s["name"]).is_file()]


def path_of(name: str) -> Path:
    path = SFX_DIR / name
    if not NAME.fullmatch(name) or not path.is_file():
        raise KeyError(name)
    return path


def _store(raw: bytes, stem: str, suffix: str, role: str, safe: bool) -> dict:
    if role not in ROLES:
        raise ValueError(f"Pick a role: {', '.join(ROLES)}.")
    if len(raw) > MAX_BYTES:
        raise ValueError("That sound is over 5 MB.")
    stem = re.sub(r"-+", "-", re.sub(r"[^A-Za-z0-9_-]", "-", stem)).strip("-")[:60] or "sound"
    SFX_DIR.mkdir(parents=True, exist_ok=True)
    target = SFX_DIR / f"{stem}{suffix}"
    probe = SFX_DIR / f"{stem}.partial{suffix}"
    probe.write_bytes(raw)
    length = _duration(probe)
    if not length:
        probe.unlink(missing_ok=True)
        raise ValueError("That is not a sound that can be played.")
    if length > MAX_SECONDS:
        probe.unlink(missing_ok=True)
        raise ValueError(f"Sound effects must be under {MAX_SECONDS:.0f} seconds.")
    probe.replace(target)
    entry = {"name": target.name, "role": role, "safe": safe, "built_in": False}
    _save([s for s in _load() if s["name"] != target.name] + [entry])
    return entry


def _fetch(url: str, limit: int) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (clipper)"})
    with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310 (host checked by the caller)
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("That page or sound is too large.")
    return data


def sound_link(page_html: str) -> str | None:
    """The first /media/sounds/*.mp3 on a myinstants page."""
    match = SOUND_LINK.search(page_html)
    return match.group(0) if match else None


def import_myinstants(url: str, role: str, safe: bool = False) -> dict:
    """Download the sound on a myinstants.com instant page (or a direct /media/sounds/ link) into the library.
    myinstants sounds are user uploads, often clipped from films and memes, so they are not marked safe."""
    url = url.strip()
    if not PAGE.match(url):
        raise ValueError("Paste a myinstants.com link, like https://www.myinstants.com/en/instant/vine-boom-sound-70972/")
    link = url.split("myinstants.com", 1)[1] if SOUND_LINK.fullmatch(url.split("myinstants.com", 1)[1]) else None
    if link is None:
        link = sound_link(_fetch(url, 2 * 1024 * 1024).decode("utf-8", errors="replace"))
        if link is None:
            raise ValueError("No sound found on that page.")
    stem = Path(link).stem
    return _store(_fetch(MYINSTANTS + link, MAX_BYTES), stem, ".mp3", role, safe)


def upload(filename: str, data: BinaryIO, role: str) -> dict:
    suffix = Path(filename).suffix.lower()
    if suffix not in (".mp3", ".m4a", ".wav"):
        raise ValueError("Upload an MP3, M4A or WAV sound.")
    return _store(data.read(MAX_BYTES + 1), Path(filename).stem, suffix, role, safe=True)


def delete(name: str) -> None:
    path_of(name).unlink()
    _save([s for s in _load() if s["name"] != name])


def pick(role: str, seed: int, safe_only: bool = False) -> str | None:
    """A sound for `role`, varied by `seed`; the user's own sounds before the built-in ones."""
    sounds = [s for s in list_sounds() if s["role"] == role and (s["safe"] or not safe_only)]
    own = [s for s in sounds if not s.get("built_in")] or sounds
    return own[seed % len(own)]["name"] if own else None


GAIN = {"whoosh": 0.35, "pop": 0.4, "impact": 0.5, "riser": 0.3, "meme": 0.6}
MIN_GAP_S = 3.0  # at most one effect in this many seconds: sound design, not a soundboard


def place(zooms: list[tuple[float, float]], emoji_starts: list[float], meme_at: float | None, duration: float,
          seed: int, safe_only: bool = False, cutaways: list[float] = ()) -> list[dict]:
    """Effects in output time: a whoosh into each B-roll cutaway and just before each punch-in lands, a pop on each
    emoji, an optional meme sound. Cutaways win a crowded moment. Pure apart from the library lookup."""
    wanted = ([(max(0.0, t - 0.25), "whoosh") for t in cutaways] + [(max(0.0, a - 0.2), "whoosh") for a, _ in zooms]
              + [(t, "pop") for t in emoji_starts])
    if meme_at is not None:
        wanted.append((meme_at, "meme"))
    events, last = [], -MIN_GAP_S
    for n, (t, role) in enumerate(sorted(wanted)):
        if t >= duration - 0.3 or (t - last < MIN_GAP_S and role != "meme"):
            continue
        name = pick(role, seed + n, safe_only)
        if name:
            events.append({"t": round(t, 3), "file": name, "gain": GAIN[role]})
            last = t
    return events
