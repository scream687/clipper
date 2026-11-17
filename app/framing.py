"""Automatic framing: follow the largest face through a clip.

Samples the preview copy a few times a second with MediaPipe, then turns the
face positions into a small number of crop keys (source time). Speaker-cut
framing for multi-person videos builds on this in a later phase.
"""

import statistics
import subprocess
import urllib.request
from pathlib import Path

import numpy as np

from app import FFMPEG, ROOT
from app.model import CropKey

MODEL = ROOT / "models" / "face_landmarker.task"
MODEL_URL = "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/latest/face_landmarker.task"
SAMPLE_FPS = 5
SAMPLE_WIDTH = 640
SMOOTH_WINDOW = 9      # samples in the median filter (about 1.8 s)
DEADBAND = 0.06        # ignore moves smaller than 6 % of the frame width
JUMP = 0.20            # a move this large between samples is a camera change: cut, don't glide
MIN_FACE_SHARE = 0.25  # fewer samples with a face than this means no usable face: show the whole frame


def keys_from_samples(samples: list[tuple[float, float | None]]) -> list[CropKey]:
    """(source time, face centre or None) -> crop keys. Pure, so it is unit-tested."""
    seen = [(t, x) for t, x in samples if x is not None]
    if not seen:
        return []
    xs = [x for _, x in seen]
    smooth = [statistics.median(xs[max(0, i - SMOOTH_WINDOW // 2): i + SMOOTH_WINDOW // 2 + 1]) for i in range(len(xs))]
    keys = [CropKey(t=seen[0][0], cx=round(smooth[0], 4))]
    for i in range(1, len(seen)):
        t, x = seen[i][0], smooth[i]
        if abs(x - keys[-1].cx) < DEADBAND:
            continue
        jump = abs(x - smooth[i - 1]) >= JUMP
        keys.append(CropKey(t=t, cx=round(x, 4), jump=jump))
    return keys


def _ensure_model() -> Path:
    if not MODEL.exists():
        MODEL.parent.mkdir(parents=True, exist_ok=True)
        partial = MODEL.with_suffix(".partial")
        urllib.request.urlretrieve(MODEL_URL, partial)
        partial.replace(MODEL)
    return MODEL


def sample_faces(proxy: Path, start: float, end: float) -> list[tuple[float, float | None]]:
    """Centre x (0..1) of the largest face at SAMPLE_FPS between start and end (source seconds)."""
    import mediapipe as mp
    from mediapipe.tasks import python
    from mediapipe.tasks.python import vision

    probe = subprocess.run([FFMPEG, "-v", "error", "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(proxy),
                            "-vf", f"fps={SAMPLE_FPS},scale={SAMPLE_WIDTH}:-2", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                           capture_output=True)
    if probe.returncode != 0:
        raise RuntimeError(f"Could not read frames for framing: {probe.stderr.decode()[-300:]}")
    # Height follows from the proxy's aspect; rawvideo has no header, so derive it from the byte count.
    ratio = _aspect(proxy)
    height = int(round(SAMPLE_WIDTH / ratio / 2)) * 2
    frame_bytes = SAMPLE_WIDTH * height * 3
    count = len(probe.stdout) // frame_bytes
    options = vision.FaceLandmarkerOptions(base_options=python.BaseOptions(model_asset_path=str(_ensure_model())),
                                           running_mode=vision.RunningMode.VIDEO, num_faces=6,
                                           min_face_detection_confidence=0.4)
    samples = []
    with vision.FaceLandmarker.create_from_options(options) as landmarker:
        for n in range(count):
            frame = np.frombuffer(probe.stdout, dtype=np.uint8, count=frame_bytes, offset=n * frame_bytes).reshape(height, SAMPLE_WIDTH, 3)
            result = landmarker.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=frame), int(n * 1000 / SAMPLE_FPS))
            best, best_area = None, 0.0
            for face in result.face_landmarks:
                xs = [p.x for p in face]; ys = [p.y for p in face]
                area = (max(xs) - min(xs)) * (max(ys) - min(ys))
                if area > best_area:
                    best, best_area = (min(xs) + max(xs)) / 2, area
            samples.append((start + n / SAMPLE_FPS, None if best is None else min(max(best, 0.0), 1.0)))
    return samples


def _aspect(proxy: Path) -> float:
    from app.media import probe
    facts = probe(proxy)
    return facts["width"] / facts["height"]


def auto_framing(proxy: Path, start: float, end: float) -> tuple[str, list[CropKey]]:
    """('crop', keys) when there is a face to follow, otherwise ('fill', []) to show the whole frame."""
    samples = sample_faces(proxy, start, end)
    with_face = sum(1 for _, x in samples if x is not None)
    if not samples or with_face / len(samples) < MIN_FACE_SHARE:
        return "fill", []
    return "crop", keys_from_samples(samples)
