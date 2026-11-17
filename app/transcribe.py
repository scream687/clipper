"""Speech to text with whisper.cpp (large-v3-turbo), on this Mac.

whisper.cpp writes each token's raw bytes into its JSON, so one Devanagari letter
can be split across two tokens and the file is not valid UTF-8 on its own. Words
are rebuilt from the bytes: a token starting with a space starts a new word.
"""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from app import FFMPEG, ROOT

MODEL = ROOT / "models" / "ggml-large-v3-turbo.bin"
VAD_MODEL = ROOT / "models" / "ggml-silero-v5.1.2.bin"
MAX_WORD_S = 1.2
LAST_WORD_S = 0.5
LogFn = Callable[[str], None]
HINGLISH_PROMPT = "हाँ तो आज हम बात करेंगे health के बारे में, ये generation कितना healthy है? मुझे लगता है कि लोग अब aware हो रहे हैं।"


def words_from_whisper_json(path: Path) -> list[dict]:
    data = json.loads(path.read_bytes().decode("utf-8", errors="surrogateescape"))
    pieces: list[tuple[bytes, float]] = []  # (bytes, start seconds)
    for segment in data.get("transcription", []):
        for tok in segment.get("tokens", []):
            raw = tok.get("text", "").encode("utf-8", errors="surrogateescape")
            if not raw or raw.startswith(b"[_"):
                continue
            dtw = tok.get("t_dtw", -1)
            start = dtw / 100 if isinstance(dtw, (int, float)) and dtw >= 0 else tok["offsets"]["from"] / 1000
            pieces.append((raw, start))

    words: list[dict] = []
    current, current_start = b"", 0.0
    for raw, start in pieces:
        if raw.startswith(b" ") and current.strip():
            words.append({"bytes": current, "start": current_start})
            current = b""
        if not current.strip():
            current_start = start
        current += raw
    if current.strip():
        words.append({"bytes": current, "start": current_start})

    result = []
    for i, w in enumerate(words):
        text = w["bytes"].decode("utf-8", errors="replace").strip()
        if not text:
            continue
        start = round(w["start"], 3)
        end = words[i + 1]["start"] if i + 1 < len(words) else start + LAST_WORD_S
        end = min(max(end, start + 0.05), start + MAX_WORD_S)
        result.append({"word": text, "start": start, "end": round(end, 3)})
    return result


def extract_audio(source: Path, wav: Path) -> Path:
    result = subprocess.run([FFMPEG, "-y", "-v", "error", "-i", str(source), "-vn", "-ac", "1", "-ar", "16000",
                             "-c:a", "pcm_s16le", str(wav)], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Could not read the audio: {result.stderr.strip()[-300:]}")
    return wav


def transcribe(source: Path, work: Path, language: str, log: LogFn) -> list[dict]:
    whisper = shutil.which("whisper-cli")
    if not whisper:
        raise RuntimeError("whisper-cli is not installed (brew install whisper-cpp).")
    if not MODEL.exists():
        raise RuntimeError(f"The Whisper model is missing at {MODEL}.")
    work.mkdir(parents=True, exist_ok=True)
    wav = extract_audio(source, work / "audio.wav")
    out = work / "whisper"
    # -mc 0: no text carried between segments, which stops repetition loops on long Hindi audio.
    cmd = [whisper, "-m", str(MODEL), "-f", str(wav), "-l", language, "-ojf", "-of", str(out),
           "-dtw", "large.v3.turbo", "-t", "8", "-mc", "0", "-np", "-pp"]
    if language == "hi":  # a Hinglish prompt keeps turbo transcribing instead of translating to English
        cmd += ["--prompt", HINGLISH_PROMPT]
    if VAD_MODEL.exists():
        cmd += ["--vad", "-vm", str(VAD_MODEL)]
    log(f"Transcribing with Whisper ({language})")
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
    for line in process.stdout:
        if "progress" in line:
            log(f"Transcribing {line.strip().split('=')[-1].strip()}")
    if process.wait() != 0:
        raise RuntimeError("Whisper could not transcribe this video.")
    words = words_from_whisper_json(out.with_suffix(".json"))
    wav.unlink(missing_ok=True)
    if not words:
        raise RuntimeError("Whisper found no speech in this video.")
    return words
