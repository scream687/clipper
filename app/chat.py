"""Where the live chat went wild: the strongest sign of a clip-worthy moment in a stream.

Chat is read from yt-dlp's `live_chat` track: the replay of a finished stream (times are video offsets), or the
running chat of a live one (times are wall-clock). Each 10 s window is scored by how many different people wrote,
with extra weight for reactions (😂, LOL, W, 🔥…) and for people asking to clip it. A spike is a window well above
the chat's own level around it, so a busy channel and a quiet one are judged alike.
"""

import json
import re
import statistics
from collections import Counter
from pathlib import Path
from typing import NamedTuple

from app.youtube import signed_in

WINDOW_S = 10        # chat is counted in windows this long
BASELINE_S = 300     # ...against the median window within this many seconds either side
HYPE_X = 2.0         # a spike is at least this many times the usual level
MIN_SCORE = 6.0      # ...and at least this busy (a 2 -> 5 jump in a dead chat is noise)
SPACING_S = 60       # one spike per minute: the strongest window wins
CHAT_LAG_S = 10      # the soonest a moment shows in chat: people type after they see it
REACTION_MAX_S = 40  # ...and the latest: viewers' players run 10-30 s behind live (measured: 35 s on CarryMinati)
MAX_PEAKS = 12

REACTION = re.compile(r"[😂🤣💀😭🔥😱🤯😳😮👀❤🥵🗿]|!!|\?\?|\b(lol+|lmf?ao+|w+|gg+|op|omg+|wtf|ha(ha)+|he(he)+|pagal|insane|"
                      r"goat|sheesh|wow+|bro+|bhai+|kya+|oh+|no+|yes+|mast|bawal|op bolte)\b", re.IGNORECASE)
CLIP_CALL = re.compile(r"\bclip\s*(it|this|that|kar|karo|krdo)\b|\bclipped\b", re.IGNORECASE)


class Msg(NamedTuple):
    t: float      # seconds: video offset (replay) or wall-clock (live)
    author: str
    text: str
    paid: bool = False


def parse_line(line: str, live: bool) -> list[Msg]:
    """The chat messages in one line of yt-dlp's live_chat JSON (malformed lines give nothing)."""
    try:
        data = json.loads(line)
        wrap = data["replayChatItemAction"]
    except (ValueError, KeyError, TypeError):
        return []
    out = []
    for action in wrap.get("actions", []):
        item = action.get("addChatItemAction", {}).get("item", {})
        r = item.get("liveChatTextMessageRenderer") or item.get("liveChatPaidMessageRenderer")
        if not r:
            continue
        parts = []
        for run in r.get("message", {}).get("runs", []):
            emoji = run.get("emoji")
            if not emoji:
                parts.append(run.get("text", ""))
            elif emoji.get("isCustomEmoji"):  # channel emotes: their :shortcut:
                parts.append((emoji.get("shortcuts") or [""])[0])
            else:
                parts.append(emoji.get("emojiId", ""))
        try:
            # a replay keeps the video offset inside the action block, yt-dlp's live writer beside it
            t = int(r["timestampUsec"]) / 1e6 if live else int(wrap.get("videoOffsetTimeMsec", data.get("videoOffsetTimeMsec"))) / 1000
        except (KeyError, ValueError, TypeError):
            continue
        author = r.get("authorExternalChannelId") or r.get("authorName", {}).get("simpleText", "")
        out.append(Msg(t, author, "".join(parts).strip(), "liveChatPaidMessageRenderer" in item))
    return out


def read_file(path: Path, live: bool = False) -> list[Msg]:
    with open(path, encoding="utf-8", errors="replace") as f:
        return [m for line in f for m in parse_line(line, live)]


def _weight(m: Msg) -> float:
    return 0.5 * bool(REACTION.search(m.text) or (m.text and len(m.text) <= 3)) + 2.0 * bool(CLIP_CALL.search(m.text)) + 2.0 * m.paid


def scores(msgs: list[Msg], start: float, end: float) -> list[float]:
    """Score of the window starting at each second from start to end: different writers plus reaction weight."""
    n = max(int(end - start), 0)
    by_second: list[list[Msg]] = [[] for _ in range(n + WINDOW_S)]
    for m in msgs:
        if 0 <= (second := int(m.t - start)) < len(by_second):
            by_second[second].append(m)
    out = []
    for i in range(n):
        window = [m for sec in by_second[i:i + WINDOW_S] for m in sec]
        out.append(len({m.author for m in window}) + sum(_weight(m) for m in window))
    return out


def peaks(msgs: list[Msg], start: float, end: float, max_peaks: int = MAX_PEAKS, min_x: float = HYPE_X) -> list[dict]:
    """Chat spikes between start and end, strongest first: {time (the moment, lag removed), chat_at, x, sample}."""
    s = scores(msgs, start, end)
    if not s:
        return []
    stride = 10  # the baseline moves slowly: a median every 10 s is plenty
    base = {i: statistics.median(s[max(0, i - BASELINE_S):i + BASELINE_S:5] or [0]) for i in range(0, len(s), stride)}
    found = []
    for i, score in enumerate(s):
        x = score / max(base[i - i % stride], 2.0)
        if score >= MIN_SCORE and x >= min_x:
            found.append((x, i))
    picked: list[tuple[float, int]] = []
    for x, i in sorted(found, reverse=True):
        if all(abs(i - j) >= SPACING_S for _, j in picked):
            picked.append((x, i))
        if len(picked) == max_peaks:
            break
    result = []
    for x, i in picked:
        at = start + i
        said = Counter(m.text.lower() for m in msgs if at <= m.t < at + WINDOW_S and 0 < len(m.text) <= 25)
        result.append({"time": round(max(at - CHAT_LAG_S, start), 1), "chat_at": round(at, 1), "x": round(x, 1),
                       "sample": [text for text, _ in said.most_common(4)]})
    return result


def _clock(seconds: float) -> str:
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"


def chat_note(chat_peaks: list[dict]) -> str:
    """The spikes, for the AI that picks moments."""
    return "\n".join(f"- {_clock(p.get('chat_at', p['time']))}: chat {p['x']}x busier than usual"
                     + (f" ({', '.join(p['sample'][:3])})" if p.get("sample") else "") for p in chat_peaks)


def hype_at(chat_peaks: list[dict], start: float, end: float) -> float | None:
    """The biggest spike whose cause can lie in start..end (the REACTION_MAX_S before the chat boomed), or None."""
    xs = [p["x"] for p in chat_peaks
          if start <= p.get("chat_at", p["time"]) + 3 and end >= p.get("chat_at", p["time"]) - REACTION_MAX_S]
    return max(xs) if xs else None


@signed_in
def fetch_replay_peaks(url: str, work: Path) -> list[dict]:
    """Download a finished stream's chat replay, find its spikes, and drop the (large) chat file."""
    import yt_dlp

    from app.youtube import _options

    work.mkdir(parents=True, exist_ok=True)
    with yt_dlp.YoutubeDL(_options(skip_download=True, writesubtitles=True, subtitleslangs=["live_chat"],
                                   outtmpl=str(work / "chat.%(ext)s"))) as ydl:
        ydl.download([url])
    path = work / "chat.live_chat.json"
    if not path.is_file():
        return []
    try:
        msgs = read_file(path)
    finally:
        path.unlink(missing_ok=True)
    if not msgs:
        return []
    return peaks(msgs, 0, max(m.t for m in msgs))
