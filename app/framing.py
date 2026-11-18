"""Automatic framing: follow the speaker through a clip.

Samples the preview copy ten times a second with MediaPipe. With one face, the
crop follows it. With several (a podcast two-shot), it follows whoever's mouth is
moving, switching only after the new speaker has talked for a while. The chosen
positions become a small number of crop keys (source time). When a camera cut
lands on a shot with no findable face (a wide shot of the set), the clip shows
the whole frame on a blurred fill until a face is back.
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
WIDE_SHOT_WIDTH = 1280
WIDE_SHOT_TILES = (3, 2)  # ...and look at it in overlapping tiles: a face 30 px wide fills far more of each one
TILE_OVERLAP = 0.25
TILE_SAME_FACE = 0.03     # faces this close (share of the width) in two tiles are one person  # stage shows and vlogs: faces are small in wide shots, so look closer before giving up
SMOOTH_WINDOW = 15     # samples in the median filter (about 1.5 s)
DEADBAND = 0.06        # ignore moves smaller than 6 % of the frame width
JUMP = 0.20            # a move this large between samples is a camera change: cut, don't glide
MIN_FACE_SHARE = 0.25  # fewer samples with a face than this means no usable face: show the whole frame
CUT_DIFF = 25.0        # mean grey-level change between samples above this is a camera cut (motion stays under ~13)
MIN_FIT_S = 0.5        # a faceless wide shot shorter than this keeps the crop


def _face_keys(seen: list[tuple[float, float]]) -> list[CropKey]:
    """Smoothed crop keys for one stretch of samples that all have a face."""
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


def _fit_spans(samples: list[tuple[float, float | None]], cuts: list[float]) -> list[tuple[float, float]]:
    """Stretches with no face that begin at a camera cut (a wide shot), as (cut time, time a face is back).

    A face lost without a cut is the speaker looking down: the crop holds instead.
    ponytail: edges land on the 0.1 s sample grid, so a switch can be one or two frames off the real
    camera cut; search the frames between samples for the exact cut if that ever shows."""
    spans, start = [], None
    for t, x in [*samples, (float("inf"), 0.0)]:
        if x is None:
            start = t if start is None else start
            continue
        if start is not None:
            cut = min((c for c in cuts if start - 1.5 / SAMPLE_FPS <= c < t), default=None)
            if cut is not None and t - cut >= MIN_FIT_S:
                spans.append((cut, t))
            start = None
    return spans


def keys_from_samples(samples: list[tuple[float, float | None]], cuts: list[float] = ()) -> list[CropKey]:
    """(source time, face centre or None) and camera-cut times -> crop keys. Pure, so it is unit-tested."""
    if not any(x is not None for _, x in samples):
        return []
    spans = _fit_spans(samples, list(cuts))
    keys: list[CropKey] = []
    lo = float("-inf")
    for n, (fit_from, fit_to) in enumerate([*spans, (float("inf"), None)]):
        part = _face_keys([(t, x) for t, x in samples if x is not None and lo <= t < fit_from])
        if part and n > 0:
            part[0] = part[0].model_copy(update={"jump": True})
        keys += part
        if fit_to is not None:
            keys.append(CropKey(t=fit_from, cx=keys[-1].cx if keys else 0.5, jump=True, fit=True))
            lo = fit_to
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


def tile_boxes(cols: int, rows: int, overlap: float = TILE_OVERLAP) -> list[tuple[float, float, float, float]]:
    """(x, y, w, h) shares of the frame: a grid of tiles that overlap so a face on a seam is whole in one of them."""
    w, h = min(1.0, (1 + overlap * (cols - 1)) / cols), min(1.0, (1 + overlap * (rows - 1)) / rows)
    xs = [0.0] if cols == 1 else [i * (1 - w) / (cols - 1) for i in range(cols)]
    ys = [0.0] if rows == 1 else [j * (1 - h) / (rows - 1) for j in range(rows)]
    return [(x, y, w, h) for y in ys for x in xs]


def merge_faces(faces: list[Face]) -> list[Face]:
    """One face per person: the same face found in two overlapping tiles keeps its larger copy. Pure."""
    kept: list[Face] = []
    for face in sorted(faces, key=lambda f: -f[1]):
        if all(abs(face[0] - k[0]) > TILE_SAME_FACE for k in kept):
            kept.append(face)
    return kept


def sample_faces(proxy: Path, start: float, end: float, width: int = SAMPLE_WIDTH,
                 tiles: tuple[int, int] = (1, 1)) -> tuple[list[tuple[float, float | None]], list[float]]:
    """Centre x (0..1) of the face to follow at SAMPLE_FPS between start and end (source seconds), and camera-cut times."""
    import mediapipe as mp
    from mediapipe.tasks import python
    from mediapipe.tasks.python import vision

    # Height follows from the proxy's aspect; rawvideo has no header.
    ratio = _aspect(proxy)
    height = int(round(width / ratio / 2)) * 2
    frame_bytes = width * height * 3
    options = vision.FaceLandmarkerOptions(base_options=python.BaseOptions(model_asset_path=str(_ensure_model())),
                                           running_mode=vision.RunningMode.VIDEO, num_faces=6,
                                           min_face_detection_confidence=0.4)
    # Frames are read one at a time: holding a whole clip's raw frames would take gigabytes for long clips.
    reader = subprocess.Popen([FFMPEG, "-v", "error", "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(proxy),
                               "-vf", f"fps={SAMPLE_FPS},scale={width}:{height}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    samples, cuts, previous, n = [], [], None, 0
    boxes = tile_boxes(*tiles)  # (1, 1): the whole frame. Stage shows: a grid, so far-away faces are big enough to find
    landmarkers = [vision.FaceLandmarker.create_from_options(options) for _ in boxes]
    try:
        while len(raw := reader.stdout.read(frame_bytes)) == frame_bytes:
            frame = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 3)
            grey = frame[::10, ::10].mean(axis=2)
            if previous is not None and float(np.abs(grey - previous).mean()) > CUT_DIFF:
                cuts.append(start + n / SAMPLE_FPS)
            previous = grey
            faces: list[Face] = []
            for landmarker, (bx, by, bw, bh) in zip(landmarkers, boxes):
                x0, y0 = round(bx * width), round(by * height)
                part = np.ascontiguousarray(frame[y0:y0 + round(bh * height), x0:x0 + round(bw * width)])
                result = landmarker.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=part), int(n * 1000 / SAMPLE_FPS))
                for face in result.face_landmarks:  # tile coordinates back to the whole frame
                    xs = [bx + p.x * bw for p in face]; ys = [by + p.y * bh for p in face]
                    face_h = max(ys) - min(ys)
                    if face_h <= 0:
                        continue
                    mouth = abs(face[LOWER_LIP].y - face[UPPER_LIP].y) * bh / face_h
                    faces.append((min(max((min(xs) + max(xs)) / 2, 0.0), 1.0), (max(xs) - min(xs)) * face_h, mouth))
            faces = merge_faces(faces) if len(boxes) > 1 else faces
            biggest = max((f[1] for f in faces), default=0)
            # Background faces (audience, posters) are ignored: keep faces at least a quarter the size of the largest.
            samples.append((start + n / SAMPLE_FPS, [f for f in faces if f[1] >= biggest / 4]))
            n += 1
    finally:
        for landmarker in landmarkers:
            landmarker.close()
        reader.stdout.close()
        error = reader.stderr.read().decode(errors="replace")
        code = reader.wait()
    if code != 0:
        raise RuntimeError(f"Could not read frames for framing: {error[-300:]}")
    return choose_speaker(samples), cuts


CAM_SAMPLES = 16     # frames looked at to find a streamer's facecam
CAM_WIDTH = 960      # facecam faces are small (~60 px here): too small for the 640 px speaker tracking
CAM_MAX_FACE = 0.12  # a face narrower than this share of the width is an overlay, not the shot itself
CAM_EDGE = 3.0       # a box border is a still edge at least this many times the median edge strength nearby
CAM_INSET = 3        # px (at CAM_WIDTH) kept off the border line itself
CAM_CORNER = 0.06    # a facecam sits in a corner: within this share of two adjacent frame edges


def cam_box(frames: np.ndarray, faces: list[tuple[float, float, float]]) -> list[float] | None:
    """[x, y, w, h] (0..1) of the facecam: the corner the small face stays in, bounded by the straight
    edges that stay put while the gameplay moves. Pure (frames: N x H x W grey, faces: (cx, cy, w) px), so it is unit-tested."""
    n, height, width = frames.shape
    if not faces or len(faces) < n / 2:
        return None
    corner = lambda f: (f[0] > width / 2, f[1] > height / 2)
    usual = statistics.mode(corner(f) for f in faces)
    fx, fy, fw = (statistics.median(f[i] for f in faces if corner(f) == usual) for i in range(3))
    if fw > CAM_MAX_FACE * width:
        return None
    gx = np.abs(np.diff(frames, axis=2)).mean(0)  # still vertical lines survive the average, moving ones fade
    gy = np.abs(np.diff(frames, axis=1)).mean(0)
    near_x = gx[max(int(fy - fw), 0):int(fy + fw)].mean(0)
    near_y = gy[:, max(int(fx - fw), 0):int(fx + fw)].mean(1)

    def border(score: np.ndarray, lo: float, hi: float, fallback: float) -> float:
        if lo <= 0 or hi >= len(score):  # the frame edge is within reach: no clear border means the cam runs to it
            fallback = 0 if lo <= 0 else len(score) + 1
        lo, hi = max(int(lo), 0), min(int(hi), len(score))
        seg = score[lo:hi]
        if seg.size and seg.max() >= CAM_EDGE * max(float(np.median(seg)), 1.0):
            return lo + int(seg.argmax())
        return fallback  # otherwise a typical facecam's margin around the face

    x0 = border(near_x, fx - 6 * fw, fx - fw, fx - 2 * fw) + CAM_INSET
    x1 = border(near_x, fx + fw, fx + 6 * fw, fx + 2 * fw) - CAM_INSET
    y0 = border(near_y, fy - 5 * fw, fy - 0.8 * fw, fy - 1.5 * fw) + CAM_INSET
    y1 = border(near_y, fy + 0.8 * fw, fy + 5 * fw, fy + 1.5 * fw) - CAM_INSET
    x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, width), min(y1, height)
    at_side = x0 <= CAM_CORNER * width or x1 >= (1 - CAM_CORNER) * width
    at_end = y0 <= CAM_CORNER * height or y1 >= (1 - CAM_CORNER) * height
    if not (at_side and at_end):
        return None  # a small face mid-frame is someone in a wide shot, not an overlay
    return [round(x0 / width, 4), round(y0 / height, 4), round((x1 - x0) / width, 4), round((y1 - y0) / height, 4)]


def find_facecam(proxy: Path, start: float, end: float) -> list[float] | None:
    """The facecam box of a gameplay or screen recording between start and end (source seconds), or None."""
    import mediapipe as mp
    from mediapipe.tasks import python
    from mediapipe.tasks.python import vision

    height = int(round(CAM_WIDTH / _aspect(proxy) / 2)) * 2
    raw = subprocess.run([FFMPEG, "-v", "error", "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(proxy),
                          "-vf", f"fps={CAM_SAMPLES / max(end - start, 1.0):.4f},scale={CAM_WIDTH}:{height}",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    frames = np.frombuffer(raw, np.uint8).reshape(-1, height, CAM_WIDTH, 3)
    options = vision.FaceLandmarkerOptions(base_options=python.BaseOptions(model_asset_path=str(_ensure_model())),
                                           running_mode=vision.RunningMode.IMAGE, num_faces=2,
                                           min_face_detection_confidence=0.4)
    faces = []
    qh, qw = height // 2, CAM_WIDTH // 2
    with vision.FaceLandmarker.create_from_options(options) as landmarker:
        for frame in frames:  # one quarter at a time: a corner facecam's face is then big enough to detect
            for y0 in (0, qh):
                for x0 in (0, qw):
                    part = np.ascontiguousarray(frame[y0:y0 + qh, x0:x0 + qw])
                    for face in landmarker.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=part)).face_landmarks:
                        xs = [p.x for p in face]; ys = [p.y for p in face]
                        faces.append((x0 + (min(xs) + max(xs)) / 2 * qw, y0 + (min(ys) + max(ys)) / 2 * qh, (max(xs) - min(xs)) * qw))
    return cam_box(frames.mean(axis=3, dtype=np.float32), faces)


def _aspect(proxy: Path) -> float:
    from app.media import probe
    facts = probe(proxy)
    return facts["width"] / facts["height"]


def auto_framing(proxy: Path, start: float, end: float, width: int = SAMPLE_WIDTH,
                 tiles: tuple[int, int] = (1, 1)) -> tuple[str, list[CropKey]]:
    """('crop', keys) when there is a face to follow, otherwise ('fill', []) to show the whole frame."""
    samples, cuts = sample_faces(proxy, start, end, width, tiles)
    with_face = sum(1 for _, x in samples if x is not None)
    if not samples or with_face / len(samples) < MIN_FACE_SHARE:
        return "fill", []
    return "crop", keys_from_samples(samples, cuts)
