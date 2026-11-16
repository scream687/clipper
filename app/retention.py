"""YouTube's "Most replayed" graph: fetch it, find its peaks, score a clip range.

The graph is a list of points ``{"start_time", "end_time", "value"}`` with
``value`` normalised to 0..1. Only videos with enough views have one.
"""

from typing import Any

Point = dict[str, float]

# The opening seconds always score ~1.0 because every viewer plays them. That is
# not a replay signal, so the intro is left out of peaks and of the baseline.
INTRO_SKIP_FRACTION = 0.05
MAX_PEAKS = 8
PEAK_MIN_GAP_SECONDS = 30.0


def fetch_heatmap(url: str, cookies_path: str | None = None) -> list[Point]:
    """Return the video's replay graph, or [] when YouTube has none for it."""
    import yt_dlp

    options: dict[str, Any] = {"skip_download": True, "quiet": True, "no_warnings": True}
    if cookies_path:
        options["cookiefile"] = cookies_path
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)
    return list(info.get("heatmap") or [])


def _after_intro(heatmap: list[Point]) -> list[Point]:
    intro_end = heatmap[-1]["end_time"] * INTRO_SKIP_FRACTION
    return [p for p in heatmap if p["start_time"] >= intro_end]


def _mean(points: list[Point]) -> float:
    return sum(p["value"] for p in points) / len(points)


def pick_peaks(
    heatmap: list[Point],
    max_peaks: int = MAX_PEAKS,
    min_gap: float = PEAK_MIN_GAP_SECONDS,
) -> list[dict[str, float]]:
    """Most-replayed moments after the intro, strongest first, at least ``min_gap`` apart.

    Only points above the video's own average count, so a flat graph has no peaks.
    """
    if not heatmap:
        return []
    body = _after_intro(heatmap)
    if not body:
        return []
    average = _mean(body)

    peaks: list[dict[str, float]] = []
    for point in sorted(body, key=lambda p: p["value"], reverse=True):
        if point["value"] <= average or len(peaks) >= max_peaks:
            break
        time = (point["start_time"] + point["end_time"]) / 2
        if all(abs(time - taken["time"]) >= min_gap for taken in peaks):
            peaks.append({"time": time, "value": point["value"]})
    return peaks


def score_range(heatmap: list[Point], start: float, end: float) -> float | None:
    """How replayed [start, end] is against the video average: 1.0 is average, 2.0 is double.

    Returns None when there is no graph or the range does not touch it.
    """
    if not heatmap or end <= start:
        return None
    body = _after_intro(heatmap)
    if not body:
        return None
    average = _mean(body)
    if average <= 0:
        return None

    weighted, covered = 0.0, 0.0
    for point in heatmap:
        overlap = min(end, point["end_time"]) - max(start, point["start_time"])
        if overlap > 0:
            weighted += point["value"] * overlap
            covered += overlap
    if covered <= 0:
        return None
    return round(weighted / covered / average, 2)


def _clock(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"


def retention_note(peaks: list[dict[str, float]]) -> str:
    """Text handed to the AI next to the transcript. Empty when there are no peaks."""
    if not peaks:
        return ""
    lines = [f"- {_clock(p['time'])} (replay intensity {p['value']:.2f})" for p in peaks]
    return (
        "VIEWER REPLAY DATA (YouTube 'Most replayed' graph):\n"
        "Viewers rewind to these moments far more than the rest of the video. "
        "Prefer segments that contain one of them when the content is strong, "
        "and start the segment early enough to include the lead-up.\n" + "\n".join(lines)
    )
