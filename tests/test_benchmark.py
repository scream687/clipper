from app.benchmark import covered, place_shorts, score
from app.model import Clip, Video, Word


def _video(n: int = 600) -> Video:
    words = [Word(id=i, text=f"w{i}", display=f"w{i}", start=float(i), end=i + 1.0) for i in range(n)]
    return Video(id="v", title="t", duration=n, fps=25, width=1, height=1, words=words)


def test_a_short_is_placed_where_its_captions_were_said_and_ads_are_not():
    video = _video()
    real = {"id": "a", "title": "real", "text": " ".join(f"w{i}" for i in range(100, 140))}
    ad = {"id": "b", "title": "ad", "text": "w300 w301 w302 w303 w304 w305 " * 3}
    placed = place_shorts([real, ad], video.words)
    assert [s["id"] for s in placed] == ["a"] and placed[0]["start"] == 100


def test_score_counts_shorts_whose_lines_are_mostly_inside_a_clip():
    video = _video()
    shorts = [{"id": "a", "title": "", "hits": [100.0, 110.0, 120.0], "start": 100, "end": 120},
              {"id": "b", "title": "", "hits": [400.0, 410.0], "start": 400, "end": 410}]
    clips = [Clip(index=0, first_word=95, last_word=125, score=9), Clip(index=1, first_word=405, last_word=450, score=2)]
    assert covered(shorts[0], [(95, 126)]) and covered(shorts[1], [(405, 451)])  # 1 of 2 lines is half
    result = score(video, clips, shorts)
    assert (result["found"], result["score"]) == (2, 10.0)
    assert score(video, clips, shorts, top=1)["found"] == 1  # only the best-scored clip counts
