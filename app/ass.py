"""The clip's text layer as an ASS subtitle file for libass (ffmpeg's `ass` filter): captions, the hook, the follow
badge, handle and credit, with the same motion as the editor preview (web/overlay/overlay.js) as far as ASS can:
caption groups blur-pop in, the AI's key words pop in the accent colour, the hook settles and fades.

Line breaks come from the plan, so libass never re-wraps (WrapStyle 2).
"""

from app.plan import Plan, line_height

BOX_ALPHA = "4D"          # 70 % opaque black behind boxed captions and the hook
SOFT_ALPHA = "26"         # handle and credit drawn at 85 % opacity
POP_MS = 200              # caption groups blur-pop in this fast (video-edit skill: scale 1.12 + blur -> 1)
EMPHASIS_MS = 220
EMPHASIS_SCALE = 118


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


def _font(name: str) -> str:
    return "Arial" if name == "Arial Bold" else name


def _style(name, font, size, primary, secondary, outline_colour, back, border_style, outline, align) -> str:
    # ASS Fontsize is the line height (ascent + descent), not the em size the plan and CSS use.
    ass_size = line_height("Arial Bold" if font == "Arial" else font, size)
    return (f"Style: {name},{font},{ass_size},{primary},{secondary},{outline_colour},{back},-1,0,0,0,"
            f"100,100,0,0,{border_style},{outline},0,{align},0,0,0,1")


def build_ass(plan: Plan) -> str:
    s = plan.style
    white, active, box = s["color"], s["active"], s["box"]
    edge = _colour("#000000", BOX_ALPHA if box else "00")
    f = s["follow"]
    styles = [
        _style("Caption", _font(s["font"]), s["size"], _colour(active if plan.look == "karaoke" else white), _colour(white),
               edge, edge, 3 if box else 1, round(s["size"] * 0.22) if box else s["outline"], 5),
        _style("Hook", _font(s["hook"]["font"]), s["hook"]["size"], _colour("#FFFFFF"), _colour("#FFFFFF"),
               _colour("#000000", BOX_ALPHA), _colour("#000000", BOX_ALPHA), 3, round(s["hook"]["size"] * 0.25), 8),
        _style("Follow", _font(f["font"]), f["size"], _colour(f["color"]), _colour(f["color"]), _colour(f["box"]),
               _colour(f["box"]), 3, round(f["size"] * 0.35), 5),
        _style("Handle", "Arial", s["handle"]["size"], _colour(s["handle"]["color"], SOFT_ALPHA), _colour("#FFFFFF"),
               _colour(s["handle"]["edge"], "80"), _colour(s["handle"]["edge"], "80"), 1, 2, 5),
        _style("Credit", "Arial", s["credit"]["size"], _colour(s["credit"]["color"], SOFT_ALPHA), _colour("#FFFFFF"),
               _colour(s["credit"]["edge"], "80"), _colour(s["credit"]["edge"], "80"), 1, 2, 1),
    ]
    head = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {plan.width}", f"PlayResY: {plan.height}",
        "WrapStyle: 2", "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        *styles, "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    return "\n".join(head + _caption_events(plan) + _extra_events(plan)) + "\n"


def _ms(t: float) -> int:
    return max(0, round(t * 1000))


def _caption_events(plan: Plan) -> list[str]:
    s = plan.style
    words = {w.word_id: w for w in plan.words}
    hot = set(plan.emphasis)
    white, active, accent = _colour(s["color"]), _colour(s["active"]), _colour(plan.accent)
    events: list[str] = []
    for group in plan.captions:
        ids = [wid for line in group.lines for wid in line.word_ids]
        nxt = {wid: (words[ids[i + 1]].start if i + 1 < len(ids) else group.end) for i, wid in enumerate(ids)}
        spans, cursor = {}, group.start  # karaoke: each word's sweep ends where the next word starts
        for wid in ids:
            spans[wid] = max(1, round((nxt[wid] - cursor) * 100))
            cursor = nxt[wid]

        def word(wid: int) -> str:
            w = words[wid]
            text = _clean(w.text.upper() if s["uppercase"] else w.text)
            at, until = _ms(w.start - group.start), _ms(nxt[wid] - group.start)
            if wid in hot:  # the AI's key words: accent colour and a blur-pop on the word, whatever the look
                tags = (f"\\c{accent}\\t({at},{at + 1},\\fscx{EMPHASIS_SCALE}\\fscy{EMPHASIS_SCALE}\\blur8)"
                        f"\\t({at + 1},{at + EMPHASIS_MS},\\fscx100\\fscy100\\blur0)")
                return f"{{{tags}}}{text}{{\\c{white}}}"
            if plan.look == "karaoke":
                return f"{{\\kf{spans[wid]}}}{text}"
            if plan.look == "highlight":
                return f"{{\\t({at},{at},\\c{active})\\t({until},{until},\\c{white})}}{text}{{\\c{white}}}"
            if plan.look == "build" and wid != ids[0]:
                return f"{{\\alpha&HFF&\\t({at},{at},\\alpha&H00&)}}{text}"
            return text

        text = "\\N".join(" ".join(word(w) for w in line.word_ids) for line in group.lines)
        pop = (f"\\an5\\pos({plan.width / 2:.0f},{plan.height * plan.caption_y:.0f})\\fscx112\\fscy112\\blur10"
               f"\\t(0,{POP_MS},0.6,\\fscx100\\fscy100\\blur0)")
        events.append(f"Dialogue: 0,{_time(group.start)},{_time(group.end)},Caption,,0,0,0,,{{{pop}}}{text}")
    return events


def _extra_events(plan: Plan) -> list[str]:
    s, end = plan.style, _time(plan.duration)
    events = []
    if plan.hook:  # fully there from the first frame (often the cover picture); it only settles, then fades
        x, y = plan.width / 2, plan.height * s["hook"]["y"]
        text = "\\N".join(_clean(line) for line in plan.hook.lines)
        events.append(f"Dialogue: 1,{_time(plan.hook.start)},{_time(plan.hook.end)},Hook,,0,0,0,,"
                      f"{{\\an8\\move({x:.0f},{y - 18:.0f},{x:.0f},{y:.0f},0,300)\\fscx104\\fscy104"
                      f"\\t(0,300,0.5,\\fscx100\\fscy100)\\fad(0,200)}}{text}")
    if plan.follow:
        f = s["follow"]
        events.append(f"Dialogue: 1,{_time(plan.follow.start)},{_time(plan.follow.end)},Follow,,0,0,0,,"
                      f"{{\\an5\\pos({plan.width / 2:.0f},{plan.height * f['y']:.0f})\\fscx60\\fscy60\\fad(250,0)"
                      f"\\t(0,300,0.5,\\fscx100\\fscy100)}}{_clean(plan.follow.lines[0])}")
    if plan.handle:
        events.append(f"Dialogue: 1,0:00:00.00,{end},Handle,,0,0,0,,"
                      f"{{\\an5\\pos({plan.width / 2:.0f},{plan.height * s['handle']['y']:.0f})}}{_clean(plan.handle)}")
    if plan.credit:
        margin = round(plan.width * 0.04)
        events.append(f"Dialogue: 1,0:00:00.00,{end},Credit,,0,0,0,,"
                      f"{{\\an1\\pos({margin},{plan.height * s['credit']['y']:.0f})}}{_clean(plan.credit)}")
    return events
