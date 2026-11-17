from app.loudness import loud_seconds, loudness_clips
from app.model import Video, Word


def test_seconds_well_above_the_videos_normal_level_are_loud():
    levels = [-30.0 + (i % 5) for i in range(100)]  # normal speech varies a little
    levels[40] = levels[41] = -12.0
    levels[70] = -26.0
    assert loud_seconds(levels) == [40, 41]


def test_silence_and_flat_audio_give_no_loud_seconds():
    assert loud_seconds([-30.0] * 50) == [] and loud_seconds([]) == []


def test_fallback_clips_sit_on_the_loudest_moments_without_overlap():
    words = [Word(id=i, text=f"w{i}", display=f"w{i}", start=i * 0.5, end=i * 0.5 + 0.4) for i in range(400)]
    video = Video(id="v", title="t", duration=200, fps=25, width=1920, height=1080, words=words, loud=[50, 51, 52, 150])
    clips = loudness_clips(video, min_s=20, max_s=40, max_clips=5)
    spans = [(words[c.first_word].start, words[c.last_word].end) for c in clips]
    assert len(clips) == 2 and spans[0][0] <= 50 <= spans[0][1] and spans[1][0] <= 150 <= spans[1][1]
    assert spans[0][1] <= spans[1][0]


def test_fallback_also_uses_replay_peaks_when_audio_is_flat():
    words = [Word(id=i, text=f"w{i}", display=f"w{i}", start=i * 0.5, end=i * 0.5 + 0.4) for i in range(400)]
    heat = [{"start_time": i * 2.0, "end_time": (i + 1) * 2.0, "value": 0.9 if i == 60 else 0.1} for i in range(100)]
    video = Video(id="v", title="t", duration=200, fps=25, width=1920, height=1080, words=words, heatmap=heat)
    clips = loudness_clips(video, min_s=20, max_s=40, max_clips=5)
    assert clips and words[clips[0].first_word].start <= 121 <= words[clips[0].last_word].end
