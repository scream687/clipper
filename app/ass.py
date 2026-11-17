"""Turn a render plan into an ASS subtitle file for libass (ffmpeg's `ass` filter).

Line breaks come from the plan, so libass never re-wraps (WrapStyle 2).
web/preview.js draws the same plan with CSS.
"""

from app.plan import Plan

ACTIVE_ALPHA_BOX = "4D"   # 70 % opaque black behind boxed captions and the hook
SOFT_ALPHA = "26"         # handle and credit drawn at 85 % opacity


def _colour(hex_rgb: str, alpha: str = "00") -> str:
    """#RRGGBB -> &HAABBGGRR (ASS colours are BGR, alpha 00 = opaque)."""
    r, g, b = hex_rgb[1:3], hex_rgb[3:5], hex_rgb[5:7]
    return f"&H{alpha}{b}{g}{r}".upper()


def _time(t: float) -> str:
    centis = max(0, round(t * 100))
    return f"{centis // 360000}:{centis // 6000 % 60:02d}:{centis // 100 % 60:02d}.{centis % 100:02d}"


def _clean(text: str) -> str:
    """Strip characters that would start override tags or line breaks."""
    return text.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")


def _style(name, font, size, primary, secondary, outline_colour, back, bold, border_style, outline, align) -> str:
    return (f"Style: {name},{font},{size},{primary},{secondary},{outline_colour},{back},{-1 if bold else 0},0,0,0,"
            f"100,100,0,0,{border_style},{outline},0,{align},0,0,0,1")


def build_ass(plan: Plan) -> str:
    s = plan.style
    font = "Arial" if s["font"] == "Arial Bold" else s["font"]
    white, active = s["color"], s["active"]
    box = s["box"]
    styles = [
        _style("Caption", font, s["size"], _colour(active if plan.look == "karaoke" else white), _colour(white),
               _colour("#000000", ACTIVE_ALPHA_BOX if box else "00"), _colour("#000000", ACTIVE_ALPHA_BOX if box else "00"),
               True, 3 if box else 1, round(s["size"] * 0.22) if box else s["outline"], 5),
        _style("Hook", s["hook"]["font"], s["hook"]["size"], _colour("#FFFFFF"), _colour("#FFFFFF"),
               _colour("#000000", ACTIVE_ALPHA_BOX), _colour("#000000", ACTIVE_ALPHA_BOX), True, 3, round(s["hook"]["size"] * 0.25), 8),
        _style("Handle", "Arial", s["handle"]["size"], _colour("#FFFFFF", SOFT_ALPHA), _colour("#FFFFFF"),
               _colour("#000000", "80"), _colour("#000000", "80"), True, 1, 2, 5),
        _style("Credit", "Arial", s["credit"]["size"], _colour("#FFFFFF", SOFT_ALPHA), _colour("#FFFFFF"),
               _colour("#000000", "80"), _colour("#000000", "80"), True, 1, 2, 1),
    ]
    head = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {plan.width}", f"PlayResY: {plan.height}",
        "WrapStyle: 2", "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        *styles, "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    events = _caption_events(plan) + _extra_events(plan)
    return "\n".join(head + events) + "\n"


def _word_text(plan: Plan, text: str) -> str:
    return _clean(text.upper() if plan.style["uppercase"] else text)


def _caption_events(plan: Plan) -> list[str]:
    s = plan.style
    words = {w.word_id: w for w in plan.words}
    pos = f"{{\\an5\\pos({plan.width / 2:.0f},{plan.height * plan.caption_y:.0f})}}"
    active, white = _colour(s["active"]), _colour(s["color"])
    events: list[str] = []
    for group in plan.captions:
        ids = [wid for line in group.lines for wid in line.word_ids]

        def render(fn) -> str:
            return "\\N".join(" ".join(fn(words[w]) for w in line.word_ids) for line in group.lines)

        if plan.look == "highlight":
            for index, wid in enumerate(ids):
                start = group.start if index == 0 else words[wid].start
                end = group.end if index == len(ids) - 1 else words[ids[index + 1]].start
                text = render(lambda w, cur=wid: f"{{\\c{active}}}{_word_text(plan, w.text)}{{\\c{white}}}"
                              if w.word_id == cur else _word_text(plan, w.text))
                events.append(f"Dialogue: 0,{_time(start)},{_time(end)},Caption,,0,0,0,,{pos}{text}")
        elif plan.look == "karaoke":
            # \kf durations run from group start; each word's span ends where the next word starts.
            spans, cursor = {}, group.start
            for index, wid in enumerate(ids):
                end = group.end if index == len(ids) - 1 else words[ids[index + 1]].start
                spans[wid] = max(1, round((end - cursor) * 100))
                cursor = end
            text = render(lambda w: f"{{\\kf{spans[w.word_id]}}}{_word_text(plan, w.text)}")
            events.append(f"Dialogue: 0,{_time(group.start)},{_time(group.end)},Caption,,0,0,0,,{pos}{text}")
        elif plan.look == "pop":
            text = render(lambda w: _word_text(plan, w.text))
            events.append(f"Dialogue: 0,{_time(group.start)},{_time(group.end)},Caption,,0,0,0,,"
                          f"{{\\an5\\pos({plan.width / 2:.0f},{plan.height * plan.caption_y:.0f})\\fscx70\\fscy70\\t(0,120,\\fscx100\\fscy100)}}{text}")
        else:  # boxed
            text = render(lambda w: _word_text(plan, w.text))
            events.append(f"Dialogue: 0,{_time(group.start)},{_time(group.end)},Caption,,0,0,0,,{pos}{text}")
    return events


def _extra_events(plan: Plan) -> list[str]:
    s, end = plan.style, _time(plan.duration)
    events = []
    if plan.hook:
        text = "\\N".join(_clean(line) for line in plan.hook.lines)
        events.append(f"Dialogue: 1,{_time(plan.hook.start)},{_time(plan.hook.end)},Hook,,0,0,0,,"
                      f"{{\\an8\\pos({plan.width / 2:.0f},{plan.height * s['hook']['y']:.0f})}}{text}")
    if plan.handle:
        events.append(f"Dialogue: 1,0:00:00.00,{end},Handle,,0,0,0,,"
                      f"{{\\an5\\pos({plan.width / 2:.0f},{plan.height * s['handle']['y']:.0f})}}{_clean(plan.handle)}")
    if plan.credit:
        margin = round(plan.width * 0.04)
        events.append(f"Dialogue: 1,0:00:00.00,{end},Credit,,0,0,0,,"
                      f"{{\\an1\\pos({margin},{plan.height * s['credit']['y']:.0f})}}{_clean(plan.credit)}")
    return events
