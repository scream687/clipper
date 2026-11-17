// Instant clip preview, driven entirely by the server's render plan (app/plan.py).
// Two <video> elements take turns so jump cuts play without a seek stall: while one
// plays a segment, the other is already parked at the start of the next.

const FONT_FAMILY = { "Arial Black": '"Clip Arial Black"', "Arial Bold": '"Clip Arial Bold"' };

// Mirrors app/plan.py interpolate(): glide between keys, hold before a jump key.
export function interpolate(keys, t) {
  if (!keys.length) return 0.5;
  if (t <= keys[0].t) return keys[0].cx;
  for (let i = 0; i + 1 < keys.length; i++) {
    const a = keys[i], b = keys[i + 1];
    if (t < b.t) {
      if (b.jump || b.t === a.t) return a.cx;
      return a.cx + (b.cx - a.cx) * (t - a.t) / (b.t - a.t);
    }
  }
  return keys[keys.length - 1].cx;
}

function seekTo(video, time) {
  return new Promise((resolve) => {
    if (Math.abs(video.currentTime - time) < 0.001 && video.readyState >= 2) { resolve(); return; }
    video.addEventListener("seeked", resolve, { once: true });
    video.currentTime = time;
  });
}

export class Preview {
  constructor({ stage, videos, fill, overlay, onTime, onEnd }) {
    Object.assign(this, { stage, videos, fill, overlay, onTime, onEnd });
    this.plan = null; this.segs = []; this.seg = 0; this.active = 0; this.playing = false;
    this.cropOverride = null; this.group = null; this.audio = null; this.beepOsc = null;
    this.fillCtx = fill.getContext("2d");
    new ResizeObserver(() => this.layout()).observe(stage);
    videos.forEach((video, which) => this.watch(video, which));
  }

  // `offset` is the source time the file starts at: 0 for the proxy, the window start for a kept clip window.
  load(src, offset = 0) {
    this.offset = offset;
    this.videos.forEach((v) => { if (v.getAttribute("src") !== src) v.src = src; });
  }

  // ----- plan and time mapping -----
  setPlan(plan) {
    const keepTime = this.time ?? 0;
    this.plan = plan;
    let offset = 0;
    this.segs = plan.segments.map((s) => {
      const seg = { start: s.start_frame / plan.fps - (this.offset || 0), end: s.end_frame / plan.fps - (this.offset || 0), offset };
      offset += seg.end - seg.start;
      return seg;
    });
    this.duration = plan.duration;
    this.stage.style.aspectRatio = `${plan.width} / ${plan.height}`;
    this.stage.style.width = `min(100%, calc(72vh * ${plan.width / plan.height}))`;
    this.buildStatic();
    this.layout();
    this.seek(Math.min(keepTime, Math.max(0, plan.duration - 0.05)));
  }

  locate(tOut) {
    for (let i = 0; i < this.segs.length; i++) {
      const s = this.segs[i];
      if (tOut < s.offset + (s.end - s.start) || i === this.segs.length - 1) {
        return { index: i, src: Math.min(s.start + Math.max(0, tOut - s.offset), s.end - 0.001) };
      }
    }
    return { index: 0, src: this.segs[0]?.start ?? 0 };
  }

  sourceTime() {
    return this.locate(this.time ?? 0).src + (this.offset || 0);
  }

  // ----- playback -----
  async seek(tOut) {
    if (!this.segs.length) return;
    const { index, src } = this.locate(tOut);
    this.seg = index;
    const current = this.videos[this.active], standby = this.videos[1 - this.active];
    await seekTo(current, src);
    this.time = tOut;
    this.draw(tOut, current);
    if (index + 1 < this.segs.length) seekTo(standby, this.segs[index + 1].start);
  }

  async play() {
    if (!this.plan || this.playing) return;
    if ((this.time ?? 0) >= this.duration - 0.05) await this.seek(0);
    this.ensureAudio();
    this.videos.forEach((v) => { v.muted = false; });
    this.playing = true;
    await this.videos[this.active].play().catch(() => { this.playing = false; });
  }

  pause() {
    this.playing = false;
    this.videos.forEach((v) => v.pause());
    this.setBeep(false);
  }

  toggle() { return this.playing ? this.pause() : this.play(); }

  watch(video, which) {
    const onFrame = (_now, meta) => {
      if (which === this.active && this.plan) {
        const seg = this.segs[this.seg];
        const tOut = seg.offset + (meta.mediaTime - seg.start);
        this.time = Math.max(0, tOut);
        this.draw(this.time, video);
        if (this.playing && meta.mediaTime >= seg.end - 0.5 / this.plan.fps) this.advance();
      }
      video.requestVideoFrameCallback(onFrame);
    };
    video.requestVideoFrameCallback(onFrame);
  }

  advance() {
    const current = this.videos[this.active], next = this.videos[1 - this.active];
    if (this.seg + 1 >= this.segs.length) {
      this.pause();
      this.time = this.duration;
      this.onEnd?.();
      return;
    }
    this.seg += 1;
    next.muted = current.muted;
    next.play();
    next.hidden = false; current.hidden = true; current.pause();
    this.active = 1 - this.active;
    if (this.seg + 1 < this.segs.length) seekTo(current, this.segs[this.seg + 1].start);
  }

  // ----- drawing -----
  layout() {
    if (!this.plan) return;
    const p = this.plan, boxW = this.stage.clientWidth, boxH = this.stage.clientHeight;
    this.scale = boxW / p.width;
    this.overlay.style.width = `${p.width}px`;
    this.overlay.style.height = `${p.height}px`;
    this.overlay.style.transform = `scale(${this.scale})`;
    if (p.framing === "fill") {
      const fit = Math.min(boxW / p.source_width, boxH / p.source_height);
      this.videoBox = { w: p.source_width * fit, h: p.source_height * fit, x: (boxW - p.source_width * fit) / 2, y: (boxH - p.source_height * fit) / 2, crop: false };
    } else {
      const k = boxW / p.crop_w;
      this.videoBox = { w: p.source_width * k, h: p.source_height * k, x: 0, y: -((p.source_height - p.crop_h) / 2) * k, crop: true, k };
    }
    this.videos.forEach((v) => { v.style.width = `${this.videoBox.w}px`; v.style.height = `${this.videoBox.h}px`; });
    this.fill.hidden = p.framing !== "fill";
    this.draw(this.time ?? 0, this.videos[this.active]);
  }

  setCropOverride(cx) {
    this.cropOverride = cx;
    this.draw(this.time ?? 0, this.videos[this.active]);
  }

  cropAt(tOut) {
    return this.cropOverride ?? interpolate(this.plan.crop_keys, tOut);
  }

  draw(tOut, video) {
    if (!this.plan || !this.videoBox) return;
    const p = this.plan, box = this.videoBox;
    let x = box.x;
    if (box.crop) {
      const left = Math.min(Math.max(this.cropAt(tOut) * p.source_width - p.crop_w / 2, 0), p.source_width - p.crop_w);
      x = -left * box.k;
    }
    const transform = `translate(${x}px, ${box.y}px)`;
    this.videos.forEach((v) => { v.style.transform = transform; });
    if (!this.fill.hidden && video.readyState >= 2) {
      const c = this.fill, ctx = this.fillCtx, ratio = Math.max(c.width / video.videoWidth, c.height / video.videoHeight);
      const w = video.videoWidth * ratio, h = video.videoHeight * ratio;
      ctx.drawImage(video, (c.width - w) / 2, (c.height - h) / 2, w, h);
    }
    this.drawOverlay(tOut);
    this.setBeep(this.playing && p.beeps.some(([a, b]) => tOut >= a && tOut < b));
    this.onTime?.(tOut);
  }

  buildStatic() {
    const p = this.plan, s = p.style;
    this.overlay.replaceChildren();
    this.group = null;
    this.captionHost = document.createElement("div");
    this.overlay.append(this.captionHost);
    this.hookEl = null;
    this.emojiEl = document.createElement("div");
    this.emojiEl.className = "ov-emoji";
    Object.assign(this.emojiEl.style, { top: `${p.emoji_y}px`, fontSize: `${Math.round(p.emoji_px * 1.0)}px` });
    this.overlay.append(this.emojiEl);
    if (p.hook) {
      const hook = document.createElement("div");
      hook.className = "ov-hook";
      Object.assign(hook.style, { top: `${p.height * s.hook.y}px`, font: `${s.hook.size}px/${s.hook.line_px}px ${FONT_FAMILY[s.hook.font]}` });
      const pad = Math.round(s.hook.size * 0.25);
      p.hook.lines.forEach((line) => {
        const row = document.createElement("div"), span = document.createElement("span");
        span.textContent = line; span.style.padding = `0 ${pad}px`;
        row.append(span); hook.append(row);
      });
      this.overlay.append(hook);
      this.hookEl = hook;
    }
    const small = (text, style, cls, place) => {
      if (!text) return;
      const node = document.createElement("div");
      node.className = `ov-small ${cls}`;
      node.textContent = text;
      Object.assign(node.style, { font: `700 ${style.size}px ${FONT_FAMILY["Arial Bold"]}`, ...place });
      this.overlay.append(node);
    };
    small(p.handle, s.handle, "ov-handle", { left: "50%", top: `${p.height * s.handle.y}px`, transform: "translate(-50%, -50%)" });
    small(p.credit, s.credit, "ov-credit", { left: `${Math.round(p.width * 0.04)}px`, top: `${p.height * s.credit.y}px`, transform: "translateY(-100%)" });
  }

  drawOverlay(t) {
    const p = this.plan, s = p.style;
    if (this.hookEl) this.hookEl.hidden = !(t >= p.hook.start && t < p.hook.end);
    const emoji = (p.emojis || []).find((e) => t >= e.start && t < e.end);
    this.emojiEl.textContent = emoji ? emoji.emoji : "";
    const group = p.captions.find((g) => t >= g.start && t < g.end) || null;
    if (group !== this.group) {
      this.group = group;
      this.captionHost.replaceChildren();
      if (!group) return;
      const words = this.wordMap || (this.wordMap = new Map());
      words.clear(); p.words.forEach((w) => words.set(w.word_id, w));
      const block = document.createElement("div");
      block.className = `ov-block ov-${p.look}${s.box ? "" : " ov-stroke"}`;
      Object.assign(block.style, {
        top: `${p.height * p.caption_y}px`, font: `${s.size}px/${s.line_px}px ${FONT_FAMILY[s.font]}`,
        textTransform: s.uppercase ? "uppercase" : "none",
      });
      block.style.setProperty("--ov-color", s.color);
      block.style.setProperty("--ov-active", s.active);
      block.style.setProperty("--ov-stroke", `${s.outline * 2}px`);
      if (p.look === "pop") block.classList.add("ov-pop");
      const pad = Math.round(s.size * 0.22);
      group.lines.forEach((line) => {
        const row = document.createElement("div");
        row.className = "ov-line";
        const run = document.createElement("span");
        run.className = "ov-run";
        if (s.box) run.style.padding = `0 ${pad}px`;
        line.word_ids.forEach((id, i) => {
          const span = document.createElement("span");
          span.className = "ov-word";
          span.dataset.start = words.get(id).start;
          span.textContent = (i ? " " : "") + words.get(id).text;
          run.append(span);
        });
        row.append(run); block.append(row);
      });
      this.captionHost.append(block);
    }
    if (!group) return;
    const spans = [...this.captionHost.querySelectorAll(".ov-word")];
    if (p.look === "highlight") {
      let current = 0;
      spans.forEach((sp, i) => { if (Number(sp.dataset.start) <= t) current = i; });
      spans.forEach((sp, i) => sp.classList.toggle("is-active", i === current));
    } else if (p.look === "karaoke") {
      spans.forEach((sp) => sp.classList.toggle("is-active", Number(sp.dataset.start) <= t));
    }
  }

  // ----- bleep -----
  ensureAudio() {
    if (!this.audio) this.audio = new (window.AudioContext || window.webkitAudioContext)();
  }

  setBeep(on) {
    if (!this.audio) return;
    this.videos.forEach((v) => { v.muted = on || !this.playing && v.muted; });
    if (on && !this.beepOsc) {
      const osc = this.audio.createOscillator(), gain = this.audio.createGain();
      osc.frequency.value = 1000; gain.gain.value = 0.15;
      osc.connect(gain).connect(this.audio.destination); osc.start();
      this.beepOsc = osc;
    } else if (!on && this.beepOsc) {
      this.beepOsc.stop(); this.beepOsc = null;
    }
  }
}
