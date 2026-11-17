"""Automatic framing: follow the speaker through a clip.

Samples the preview copy ten times a second with MediaPipe. With one face, the
crop follows it. With several (a podcast two-shot), it follows whoever's mouth is
moving, switching only after the new speaker has talked for a while. The chosen
positions become a small number of crop keys (source time).
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
SAMPLE_FPS = 10        # mouth movement needs more than 5 samples a second
SPEAKER_WINDOW_S = 1.0  # mouth movement is measured over this window
MIN_SHOT_S = 1.5        # a new speaker must lead for this long before the crop cuts to them
SPEAKER_MARGIN = 1.3    # ...and move their mouth this many times more than the current one
TRACK_MATCH = 0.12      # faces within this x distance between samples are the same person
UPPER_LIP, LOWER_LIP = 13, 14
SAMPLE_WIDTH = 640
SMOOTH_WINDOW = 15     # samples in the median filter (about 1.5 s)
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


Face = tuple[float, float, float]  # (centre x 0..1, area, mouth opening relative to face height)


def choose_speaker(samples: list[tuple[float, list[Face]]]) -> list[tuple[float, float | None]]:
    """Pick one face per sample: the only face, or in a multi-face shot the one that is talking. Pure, so it is unit-tested."""
    # 1. Track people across samples by position, so "the left person" keeps one identity.
    tracks: list[list[tuple[int, Face]]] = []
    per_sample: list[list[int]] = []
    for n, (_, faces) in enumerate(samples):
        ids = []
        for face in faces:
            best = min((abs(tr[-1][1][0] - face[0]), k) for k, tr in enumerate(tracks)) if tracks else (1.0, -1)
            if best[0] <= TRACK_MATCH and (not tracks[best[1]] or tracks[best[1]][-1][0] != n):
                tracks[best[1]].append((n, face)); ids.append(best[1])
            else:
                tracks.append([(n, face)]); ids.append(len(tracks) - 1)
        per_sample.append(ids)

    half = max(1, int(SPEAKER_WINDOW_S * SAMPLE_FPS / 2))

    def activity(track: int, n: int) -> float:
        opens = [f[2] for m, f in tracks[track] if abs(m - n) <= half]
        if len(opens) < 2:
            return 0.0
        mean = sum(opens) / len(opens)
        return (sum((o - mean) ** 2 for o in opens) / len(opens)) ** 0.5

    chosen: list[tuple[float, float | None]] = []
    current, challenger, since = None, None, 0.0
    for n, (t, faces) in enumerate(samples):
        ids = per_sample[n]
        if not ids:
            chosen.append((t, None)); continue
        if len(ids) == 1:
            current, challenger = ids[0], None
        else:
            loudest = max(ids, key=lambda k: activity(k, n))
            if current not in ids:
                current = loudest
            elif loudest != current and activity(loudest, n) > SPEAKER_MARGIN * activity(current, n):
                if challenger != loudest:
                    challenger, since = loudest, t
                if t - since >= MIN_SHOT_S:
                    current, challenger = loudest, None
            else:
                challenger = None
        face = faces[ids.index(current)]
        chosen.append((t, face[0]))
    return chosen


def _ensure_model() -> Path:
    if not MODEL.exists():
        MODEL.parent.mkdir(parents=True, exist_ok=True)
        partial = MODEL.with_suffix(".partial")
        urllib.request.urlretrieve(MODEL_URL, partial)
        partial.replace(MODEL)
    return MODEL


def sample_faces(proxy: Path, start: float, end: float) -> list[tuple[float, float | None]]:
    """Centre x (0..1) of the face to follow at SAMPLE_FPS between start and end (source seconds)."""
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
            faces: list[Face] = []
            for face in result.face_landmarks:
                xs = [p.x for p in face]; ys = [p.y for p in face]
                face_h = max(ys) - min(ys)
                if face_h <= 0:
                    continue
                mouth = abs(face[LOWER_LIP].y - face[UPPER_LIP].y) / face_h
                faces.append((min(max((min(xs) + max(xs)) / 2, 0.0), 1.0), (max(xs) - min(xs)) * face_h, mouth))
            biggest = max((f[1] for f in faces), default=0)
            # Background faces (audience, posters) are ignored: keep faces at least a quarter the size of the largest.
            samples.append((start + n / SAMPLE_FPS, [f for f in faces if f[1] >= biggest / 4]))
    return choose_speaker(samples)


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
