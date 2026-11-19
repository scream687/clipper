import json

from app import chat
from app.chat import Msg


def _steady(start, end, per_10s=4):
    """A normal chat: a few different people every ten seconds."""
    return [Msg(t, f"u{int(t)}", "bhai kab tak stream") for t in range(start, end, 10 // per_10s)]


def test_a_burst_of_reactions_is_a_spike_with_the_moment_before_it():
    msgs = _steady(0, 900) + [Msg(400 + k * 0.3, f"fan{k}", "😂😂 LOL") for k in range(30)]
    [peak] = chat.peaks(msgs, 0, 900)
    assert 395 <= peak["chat_at"] <= 402 and peak["time"] == peak["chat_at"] - chat.CHAT_LAG_S
    assert peak["x"] >= chat.HYPE_X and "😂😂 lol" in peak["sample"]


def test_one_spammer_is_not_hype_and_a_quiet_chat_has_no_spikes():
    spam = [Msg(400 + k * 0.2, "bot", "$250,000 instantly") for k in range(50)]
    assert chat.peaks(_steady(0, 900) + spam, 0, 900) == []
    assert chat.peaks(_steady(0, 900), 0, 900) == []


def test_clip_calls_count_extra():
    calls = [Msg(300 + k, f"v{k}", "clip it") for k in range(4)]
    plain = [Msg(300 + k, f"v{k}", "hello there") for k in range(4)]
    assert sum(chat.scores(calls, 290, 310)) > sum(chat.scores(plain, 290, 310))


def test_yt_dlp_lines_parse_for_replay_and_live():
    def line(offset, usec, runs, paid=False):
        kind = "liveChatPaidMessageRenderer" if paid else "liveChatTextMessageRenderer"
        return json.dumps({"replayChatItemAction": {"actions": [{"addChatItemAction": {"item": {kind: {
            "message": {"runs": runs}, "timestampUsec": str(usec), "authorExternalChannelId": "UC1"}}}}]},
            "videoOffsetTimeMsec": str(offset)})
    runs = [{"text": "W "}, {"emoji": {"emojiId": "😂"}}, {"emoji": {"isCustomEmoji": True, "shortcuts": [":yt:"]}}]
    [m] = chat.parse_line(line(65000, 1795020608843000, runs), live=False)
    assert m == Msg(65.0, "UC1", "W 😂:yt:", False)
    [m] = chat.parse_line(line(-2000, 1795020608843000, runs, paid=True), live=True)
    assert abs(m.t - 1795020608.843) < 1e-3 and m.paid
    assert chat.parse_line("not json", live=True) == []
    replay = json.loads(line(0, 1, runs)); replay["replayChatItemAction"]["videoOffsetTimeMsec"] = "125500"
    del replay["videoOffsetTimeMsec"]  # a finished stream's replay keeps it inside the action block
    assert chat.parse_line(json.dumps(replay), live=False)[0].t == 125.5


def test_hype_lands_on_the_clip_it_belongs_to():
    p = [{"time": 100.0, "x": 3.2}, {"time": 500.0, "x": 5.0}]
    assert chat.hype_at(p, 90, 120) == 3.2 and chat.hype_at(p, 200, 300) is None


def test_the_pick_prompt_leads_with_chat_spikes_and_marks_the_stretch_before_them():
    from app.moments import build_prompt
    from app.sentences import split_sentences
    video = _talk(30).model_copy(update={"chat_peaks": [{"time": 90.0, "chat_at": 100.0, "x": 4.2, "sample": ["😂😂", "lol"]}]})
    prompt = build_prompt(video, split_sentences(video.words), 20, 60, 5, "")
    assert "THE LIVE CHAT EXPLODED" in prompt and "1:40: chat 4.2x busier than usual (😂😂, lol)" in prompt
    marked = [line.split("]")[0].split()[1] for line in prompt.splitlines() if line.startswith("[s") and "(CHAT HYPE)" in line]
    assert marked[0] == "0:01:00" and marked[-1] == "0:01:40"  # the 40 s before the boom, where its cause is


def test_messages_at_the_edges_of_a_fractional_range_are_safe():
    msgs = [Msg(100.0 + k * 0.5, f"u{k}", "hi") for k in range(40)]
    assert len(chat.scores(msgs, 100.4, 110.9)) == 10  # live: start and end are wall-clock with fractions


def _talk(n=60, gap_at=None):
    """A sentence every 4 s (two words each), optionally silent from gap_at on."""
    from app.model import Video, Word
    words = []
    for k in range(n):
        t = k * 4.0
        if gap_at is not None and t >= gap_at:
            break
        words += [Word(id=2 * k, text="bhai", display="bhai", start=t, end=t + 0.8),
                  Word(id=2 * k + 1, text="dekho.", display="dekho.", start=t + 1.0, end=t + 1.8)]
    return Video(id="v", title="Stream", duration=n * 4, fps=30, width=1280, height=720, words=words)


def test_a_boom_clip_covers_the_reaction_delay_and_starts_at_an_echoed_line():
    from app.model import Word
    from app.moments import BOOM_BEFORE_S, BOOM_MIN_S, BOOM_REACT_S, BOOM_SETUP_S, boom_window
    video = _talk()
    by = {w.id: w for w in video.words}
    peak = {"time": 100.0, "chat_at": 110.0, "x": 4.0, "sample": ["😂😂"]}
    first, last = boom_window(video, peak, 60)
    assert 110 - BOOM_BEFORE_S <= by[first].start <= 110 - BOOM_BEFORE_S + 4
    assert 110 + BOOM_REACT_S - 2 <= by[last].end <= 110 + BOOM_REACT_S + 4
    # the chat repeats what he said at 92 s ("L GTA 6"): open BOOM_SETUP_S before that line
    words = [w if w.id != 46 else Word(id=46, text="L", display="L", start=92.0, end=92.5) for w in video.words]
    first, _ = boom_window(video.model_copy(update={"words": words}), {**peak, "sample": ["l gta 6", "ll"]}, 60)
    assert 92 - BOOM_SETUP_S <= by[first].start <= 92 - BOOM_SETUP_S + 4
    # quiet gameplay before the boom: reach back for speech so the clip is long enough to follow
    sparse = video.model_copy(update={"words": [w for w in video.words if w.start < 30 or w.start >= 104]})
    first, last = boom_window(sparse, peak, 60)
    sb = {w.id: w for w in sparse.words}
    assert sb[last].end - sb[first].start >= BOOM_MIN_S
    assert boom_window(_talk(gap_at=40), peak, 60) is None  # nobody speaks anywhere near the boom


def test_boom_clips_are_named_by_the_ai_but_cut_by_the_chat():
    from app.moments import clips_at_booms
    video = _talk().model_copy(update={"chat_peaks": [{"time": 100.0, "chat_at": 110.0, "x": 4.0, "sample": ["😂😂"]},
                                                      {"time": 200.0, "chat_at": 210.0, "x": 2.6, "sample": ["W"]}]})
    prompts = []
    def ask(prompt, schema, system):
        prompts.append(prompt)
        return {"title": "Chat lost it", "hooks": ["Bhai ye kya tha"], "description": "d", "hashtags": "#gta6"}
    clips = clips_at_booms(video, ask, 60)
    assert [round(c.score) for c in clips] == [9, 8] and clips[0].title == "Chat lost it"
    assert "never invent" in prompts[0] and "😂😂" in prompts[0] and "bhai dekho." in prompts[0]


def test_a_wave_of_bot_commands_is_a_raffle_not_hype():
    def line(text):
        return json.dumps({"replayChatItemAction": {"actions": [{"addChatItemAction": {"item": {"liveChatTextMessageRenderer": {
            "message": {"runs": [{"text": text}]}, "timestampUsec": "1", "authorExternalChannelId": "UC1"}}}}]},
            "videoOffsetTimeMsec": "1000"})
    assert chat.parse_line(line("!giveaway"), live=False) == []
    assert chat.parse_line(line("W stream!"), live=False)[0].text == "W stream!"
