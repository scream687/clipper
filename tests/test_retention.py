from app.retention import pick_peaks, retention_note, score_range


def graph(values, step=10.0):
    """Build a heatmap of equal-width points from a list of values."""
    return [
        {"start_time": i * step, "end_time": (i + 1) * step, "value": v}
        for i, v in enumerate(values)
    ]


def test_intro_spike_is_never_picked():
    # Arrange: 100 points, everyone watches the first 10 seconds
    values = [1.0] + [0.2] * 99
    values[50] = 0.6

    # Act
    peaks = pick_peaks(graph(values))

    # Assert
    assert [p["time"] for p in peaks] == [505.0]


def test_peaks_are_strongest_first_and_spaced_apart():
    values = [0.1] * 100
    values[30], values[31], values[60], values[80] = 0.9, 0.8, 0.7, 0.5

    peaks = pick_peaks(graph(values), min_gap=30.0)

    # 31 sits 10s from 30, so it is suppressed as part of the same moment
    assert [p["time"] for p in peaks] == [305.0, 605.0, 805.0]
    assert [p["value"] for p in peaks] == [0.9, 0.7, 0.5]


def test_below_average_points_are_not_peaks():
    assert pick_peaks(graph([0.3] * 100)) == []


def test_peak_count_is_capped():
    values = [0.1] * 100
    for i in range(10, 100, 10):
        values[i] = 0.9

    assert len(pick_peaks(graph(values), max_peaks=4)) == 4


def test_no_graph_gives_no_peaks_and_no_score():
    assert pick_peaks([]) == []
    assert score_range([], 0.0, 30.0) is None


def test_score_is_one_on_a_flat_graph():
    assert score_range(graph([0.4] * 100), 200.0, 260.0) == 1.0


def test_score_is_relative_to_the_video_average():
    values = [0.2] * 100
    for i in range(40, 46):
        values[i] = 0.8
    heatmap = graph(values)
    average = sum(values[5:]) / len(values[5:])  # intro excluded from the baseline

    assert score_range(heatmap, 400.0, 460.0) == round(0.8 / average, 2)


def test_score_weights_partial_overlap_by_time():
    values = [0.2] * 100
    values[40] = 1.0
    heatmap = graph(values)
    average = sum(values[5:]) / len(values[5:])

    # 5s of the 1.0 point and 5s of a 0.2 point
    assert score_range(heatmap, 405.0, 415.0) == round(0.6 / average, 2)


def test_note_lists_peak_times_as_clock_stamps():
    note = retention_note([{"time": 754.0, "value": 0.83}])

    assert "00:12:34" in note
    assert "0.83" in note


def test_note_is_empty_without_peaks():
    assert retention_note([]) == ""
