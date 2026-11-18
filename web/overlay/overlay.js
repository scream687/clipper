// The clip's text layer as one paused GSAP timeline, built from the render plan (app/plan.py).
// HyperFrames renders this page to a transparent WebM for the export; the editor preview loads the same page in
// an iframe and seeks the same timeline, so what you see while editing is what gets exported.
/* global gsap */
(function () {
  const FONT = { "Arial Black": '"Clip Arial Black"', "Arial Bold": '"Clip Arial Bold"' };
  const POP_S = 0.14;        // caption groups and emojis spring in this fast
  const EMPHASIS_SCALE = 1.18;
  let timeline = null;

  function node(tag, cls, parent, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    parent.append(n);
    return n;
  }

  function captions(p, root, tl) {
    const s = p.style, words = new Map(p.words.map((w) => [w.word_id, w]));
    const emphasis = new Set(p.emphasis || []);
    p.captions.forEach((g, gi) => {
      const block = node("div", `ov-block ov-${p.look}${s.box ? "" : " ov-stroke"}`, root);
      Object.assign(block.style, { top: `${p.height * p.caption_y}px`, font: `${s.size}px/${s.line_px}px ${FONT[s.font]}`,
                                   textTransform: s.uppercase ? "uppercase" : "none" });
      block.style.setProperty("--ov-color", s.color);
      block.style.setProperty("--ov-stroke", `${s.outline * 2}px`);
      const pad = Math.round(s.size * 0.22);
      const spans = [];
      g.lines.forEach((line) => {
        const run = node("span", "ov-run", node("div", "ov-line", block));
        if (s.box) run.style.padding = `0 ${pad}px`;
        line.word_ids.forEach((id, i) => {
          const span = node("span", "ov-word", run, (i ? " " : "") + words.get(id).text);
          spans.push({ span, word: words.get(id), hot: emphasis.has(id) });
        });
      });
      gsap.set(block, { xPercent: -50, yPercent: -50 });
      // Blur-pop in (video-edit skill: scale 1.22 + blur -> 1 in .2 s, power3.out); tl.set lands 2 ms early so it
      // shows on the word's first frame instead of one frame late.
      tl.set(block, { opacity: 1 }, Math.max(0, g.start - 0.002));
      tl.fromTo(block, { scale: 1.12, filter: "blur(10px)" }, { scale: 1, filter: "blur(0px)", duration: 0.2, ease: "power3.out", immediateRender: false }, g.start);
      tl.set(block, { opacity: 0 }, g.end);
      spans.forEach(({ span, word, hot }, i) => {
        const next = spans[i + 1]?.word.start ?? g.end;
        if (p.look === "build" && i > 0) {
          gsap.set(span, { opacity: 0 });
          tl.set(span, { opacity: 1 }, word.start);
        }
        if (hot) {  // the AI's key words: accent colour and a bounce, whatever the look
          gsap.set(span, { color: p.accent });
          tl.fromTo(span, { scale: EMPHASIS_SCALE, filter: "blur(8px)" }, { scale: 1, filter: "blur(0px)", duration: 0.22, ease: "power3.out", immediateRender: false }, word.start);
        }
        if (p.look === "highlight" && !hot) {
          tl.set(span, { color: s.active }, word.start);
          tl.set(span, { color: s.color }, next);
        } else if (p.look === "karaoke" && !hot) {
          tl.set(span, { color: s.active }, word.start);
        }
      });
    });
  }

  function hook(p, root, tl) {
    if (!p.hook) return;
    const s = p.style.hook, el = node("div", "ov-hook", root);
    Object.assign(el.style, { top: `${p.height * s.y}px`, font: `${s.size}px/${s.line_px}px ${FONT[s.font]}` });
    const pad = Math.round(s.size * 0.25);
    p.hook.lines.forEach((line) => { node("span", "", node("div", "", el), line).style.padding = `0 ${pad}px`; });
    gsap.set(el, { xPercent: -50 });
    // Fully visible from the first frame (often the cover picture); it only settles into place.
    tl.fromTo(el, { opacity: 1, y: -18, scale: 1.04 }, { opacity: 1, y: 0, scale: 1, duration: 0.3, ease: "power3.out" }, p.hook.start);
    tl.to(el, { opacity: 0, duration: 0.2, ease: "power2.in" }, Math.max(p.hook.start, p.hook.end - 0.2));
  }

  function follow(p, root, tl) {
    if (!p.follow) return;
    const f = p.style.follow, el = node("div", "ov-follow", root, p.follow.lines[0]);
    const pad = Math.round(f.size * 0.35);
    Object.assign(el.style, { top: `${p.height * f.y}px`, font: `${f.size}px ${FONT[f.font]}`, color: f.color,
                              background: f.box, padding: `${pad}px ${pad * 1.4}px` });
    gsap.set(el, { xPercent: -50, yPercent: -50 });
    tl.fromTo(el, { opacity: 0, scale: 0.6 }, { opacity: 1, scale: 1, duration: 0.3, ease: "back.out(2)" }, p.follow.start);
  }

  function emojis(p, root, tl) {
    (p.emojis || []).forEach((e) => {
      // The export draws them from pictures (app/compose.py); the preview uses the Mac's emoji font.
      const picture = p.emoji_images?.[e.emoji];
      const el = node("div", "ov-emoji", root, picture ? null : e.emoji);
      if (picture) Object.assign(node("img", "", el), { src: picture, alt: "" });
      Object.assign(el.style, { top: `${p.emoji_y}px`, fontSize: `${p.emoji_px}px`, width: picture ? `${p.emoji_px}px` : "",
                                height: picture ? `${p.emoji_px}px` : "", fontFamily: picture ? "" : '"Apple Color Emoji"' });
      gsap.set(el, { xPercent: -50, yPercent: -50 });
      tl.fromTo(el, { opacity: 0, scale: 0.3, rotation: -12 }, { opacity: 1, scale: 1, rotation: 0, duration: POP_S * 1.6, ease: "back.out(3)" }, e.start);
      tl.to(el, { opacity: 0, scale: 0.8, duration: 0.12 }, Math.max(e.start, e.end - 0.12));
    });
  }

  function small(p, root) {
    const s = p.style;
    const place = (text, style, css) => {
      if (!text) return;
      const el = node("div", "ov-small", root, text);
      Object.assign(el.style, { font: `700 ${style.size}px ${FONT["Arial Bold"]}`, color: style.color, ...css });
    };
    place(p.handle, s.handle, { left: "50%", top: `${p.height * s.handle.y}px`, transform: "translate(-50%, -50%)" });
    place(p.credit, s.credit, { left: `${Math.round(p.width * 0.04)}px`, top: `${p.height * s.credit.y}px`, transform: "translateY(-100%)" });
  }

  function progress(p, root, tl) {
    if (!p.progress) return;
    const bar = node("div", "ov-progress", root);
    // At the top edge: the bottom of a Reel or Short sits under the platform's caption and buttons.
    Object.assign(bar.style, { height: `${Math.max(4, Math.round(p.width / 135))}px`, background: p.accent, top: "0", bottom: "auto" });
    tl.fromTo(bar, { scaleX: 0 }, { scaleX: 1, duration: p.duration, ease: "none" }, 0);
  }

  // Alone (the editor preview): builds into #root with its own timeline. Inside the whole-clip composition
  // (app/compose.py): adds its tweens to the clip's timeline, in the element it is given.
  function build(p, { root = document.getElementById("root"), tl = null } = {}) {
    root.replaceChildren();
    Object.assign(root.style, { width: `${p.width}px`, height: `${p.height}px` });
    const own = !tl;
    if (own) {
      root.dataset.width = p.width; root.dataset.height = p.height; root.dataset.duration = p.duration;
      timeline?.kill();
      tl = gsap.timeline({ paused: true });
    }
    captions(p, root, tl); hook(p, root, tl); follow(p, root, tl); emojis(p, root, tl); small(p, root); progress(p, root, tl);
    if (own) {
      timeline = tl;
      window.__timelines = { overlay: tl };
      tl.seek(0, false);
    }
    return tl;
  }

  window.clipOverlay = { build, seek: (t) => timeline?.seek(t, false) };
})();
