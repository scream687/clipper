// Instant clip preview, driven entirely by the server's render plan (app/plan.py).
// Two <video> elements take turns so jump cuts play without a seek stall: while one
// plays a segment, the other is already parked at the start of the next.


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
  constructor({ stage, videos, fill, split, overlay, onTime, onEnd, maxVh = 72 }) {
    Object.assign(this, { stage, videos, fill, split, overlay, onTime, onEnd, maxVh });
    this.splitCtx = split.getContext("2d"); this.splitOverride = {};
    this.plan = null; this.segs = []; this.seg = 0; this.active = 0; this.playing = false;
    this.cropOverride = null; this.audio = null; this.beepOsc = null;
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
    this.splitOverride = {};  // a dragged split position is in the new plan now
    let offset = 0;
    this.segs = plan.segments.map((s) => {
      const seg = { start: s.start_frame / plan.fps - (this.offset || 0), end: s.end_frame / plan.fps - (this.offset || 0), offset };
      offset += seg.end - seg.start;
      return seg;
    });
    this.duration = plan.duration;
    this.stage.style.aspectRatio = `${plan.width} / ${plan.height}`;
    this.stage.style.width = `min(100%, calc(${this.maxVh}vh * ${plan.width / plan.height}))`;
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
    this.music?.pause();
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
    // Whole frame: the card rectangle from the plan (app/plan.py _canvas), with its rounded corners.
    const c = p.canvas, s = this.scale;
    this.fitBox = { w: c.card_w * s, h: c.card_h * s, x: c.card_x * s, y: c.card_y * s, crop: false, radius: c.radius * s };
    this.stage.style.background = c.background === "color" ? c.color
      : c.background === "gradient" ? `linear-gradient(to bottom, ${c.color}, ${c.color2})` : "";
    const k = boxW / p.crop_w;
    this.cropBox = { w: p.source_width * k, h: p.source_height * k, x: 0, y: -((p.source_height - p.crop_h) / 2) * k, crop: true, k };
    this.videoBox = null;
    this.draw(this.time ?? 0, this.videos[this.active]);
  }

  // Whole frame on a blurred fill: the whole clip, or a faceless wide shot inside a cropped clip (unless the crop is being dragged).
  fitAt(tOut) {
    const p = this.plan;
    return p.framing === "fill" || (this.cropOverride == null && p.fit_spans.some(([a, b]) => tOut >= a && tOut < b));
  }

  setCropOverride(cx) {
    this.cropOverride = cx;
    this.draw(this.time ?? 0, this.videos[this.active]);
  }

  cropAt(tOut) {
    return this.cropOverride ?? interpolate(this.plan.crop_keys, tOut);
  }

  // Split: the same frame drawn twice, facecam crop on top, video crop below (mirrors app/render.py _split_graph).
  splitAt(half) {
    return this.splitOverride[half] ?? (half === "top" ? this.plan.split.top : this.plan.split.bottom_cx);
  }

  setSplitOverride(half, value) {
    this.splitOverride = value == null ? {} : { [half]: value };
    this.draw(this.time ?? 0, this.videos[this.active]);
  }

  drawSplit(video) {
    const p = this.plan, sp = p.split, c = this.split, dpr = window.devicePixelRatio || 1;
    const w = Math.round(this.stage.clientWidth * dpr), h = Math.round(this.stage.clientHeight * dpr);
    if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
    if (video.readyState < 2) return;
    const k = video.videoWidth / p.source_width, topH = Math.round(h * sp.top_h / p.height);
    const [x, y, cw, ch] = this.splitAt("top");
    this.splitCtx.drawImage(video, x * k, y * k, cw * k, ch * k, 0, 0, w, topH);
    const [lo, hi] = sp.bottom_x, left = Math.min(Math.max(Math.round(this.splitAt("bottom") * p.source_width - sp.crop_w / 2), lo), hi);
    const t = this.time ?? 0, z = (p.zooms || []).some(([a, b]) => t >= a && t < b) ? p.zoom : 1;  // punch-in on the lower pane
    const zw = sp.crop_w / z, zh = sp.crop_h / z;
    this.splitCtx.drawImage(video, (left + (sp.crop_w - zw) / 2) * k, ((p.source_height - sp.crop_h) / 2 + (sp.crop_h - zh) / 2) * k,
                            zw * k, zh * k, 0, topH, w, h - topH);
  }

  draw(tOut, video) {
    if (!this.plan || !this.cropBox) return;
    const isSplit = this.plan.framing === "split";
    this.split.hidden = !isSplit;
    if (isSplit) {
      this.fill.hidden = true;
      this.drawSplit(video);
      this.drawOverlay(tOut);
      this.setBeep(this.playing && this.plan.beeps.some(([a, b]) => tOut >= a && tOut < b));
      this.onTime?.(tOut);
      return;
    }
    const p = this.plan, box = this.fitAt(tOut) ? this.fitBox : this.cropBox;
    if (box !== this.videoBox) {
      this.videoBox = box;
      this.videos.forEach((v) => {
        Object.assign(v.style, { width: `${box.w}px`, height: `${box.h}px`, borderRadius: box.crop ? "" : `${box.radius}px` });
      });
      this.fill.hidden = box.crop || p.canvas.background !== "blur";
    }
    let x = box.x, zoom = "";
    const zoomed = (p.zooms || []).some(([a, b]) => tOut >= a && tOut < b);
    if (!box.crop && zoomed && p.framing === "fill") {  // the whole card punches in (app/render.py stage_box)
      const ax = this.stage.clientWidth / 2, ay = this.stage.clientHeight * p.zoom_anchor_y;
      zoom = `translate(${ax}px, ${ay}px) scale(${p.zoom}) translate(${-ax}px, ${-ay}px) `;
    }
    if (box.crop) {
      const left = Math.min(Math.max(this.cropAt(tOut) * p.source_width - p.crop_w / 2, 0), p.source_width - p.crop_w);
      x = -left * box.k;
      // Punch-in: scale about (centre, zoom_anchor_y), the point app/render.py's narrowed crop keeps still.
      if ((p.zooms || []).some(([a, b]) => tOut >= a && tOut < b)) {
        const ax = this.stage.clientWidth / 2, ay = this.stage.clientHeight * p.zoom_anchor_y;
        zoom = `translate(${ax}px, ${ay}px) scale(${p.zoom}) translate(${-ax}px, ${-ay}px) `;
      }
    }
    const transform = `${zoom}translate(${x}px, ${box.y}px)`;
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

  // The text layer is web/overlay/ in an iframe; the export draws the same plan with ffmpeg (app/ass.py).
  // Only the post header and the logo (ffmpeg overlays them as pictures) are drawn here.
  ensureFrame() {
    if (this.frame) return;
    this.frame = document.createElement("iframe");
    this.frame.className = "ov-frame";
    this.frame.setAttribute("aria-hidden", "true");
    this.frame.tabIndex = -1;
    this.frameReady = new Promise((resolve) => this.frame.addEventListener("load", resolve, { once: true }));
    this.frame.src = "/overlay/stage.html";
    this.overlay.append(this.frame);
  }

  buildStatic() {
    const p = this.plan;
    this.ensureFrame();
    [...this.overlay.children].forEach((child) => { if (child !== this.frame) child.remove(); });
    Object.assign(this.frame.style, { width: `${p.width}px`, height: `${p.height}px` });
    const plan = p;
    this.layerReady = false;
    this.frameReady.then(() => {
      if (this.plan !== plan) return;  // a newer plan is on its way
      this.frame.contentWindow.clipOverlay.build(plan);
      this.layerReady = true;
      this.frame.contentWindow.clipOverlay.seek(this.time ?? 0);
    });
    if (p.tweet && this.tweetUrl) {  // the same PNG the export overlays (app/template.py)
      const header = document.createElement("img");
      header.className = "ov-logo"; header.alt = "";
      header.src = `${this.tweetUrl}?v=${encodeURIComponent(JSON.stringify([p.tweet.name, p.tweet.handle, p.tweet.lines, p.tweet.theme, this.logoVersion ||= Date.now()]))}`;
      Object.assign(header.style, { left: "0px", top: `${p.tweet.y}px`, width: `${p.width}px`, height: `${p.tweet.height}px` });
      this.overlay.append(header);
    }
    // B-roll and cards are rendered by Remotion at export; the preview marks where they go.
    this.insertEl = document.createElement("div");
    this.insertEl.className = "ov-insert"; this.insertEl.hidden = true;
    this.overlay.prepend(this.insertEl);
    if (p.logo) {
      const img = document.createElement("img");
      img.className = "ov-logo"; img.alt = ""; img.src = `/api/logo.png?v=${this.logoVersion ||= Date.now()}`;
      Object.assign(img.style, { left: `${p.logo.x}px`, top: `${p.logo.y}px`, width: `${p.logo.w}px`, height: `${p.logo.h}px` });
      this.overlay.append(img);
    }
  }

  drawOverlay(t) {
    if (this.layerReady) this.frame.contentWindow.clipOverlay.seek(t);
    const insert = (this.plan.inserts || []).find((i) => t >= i.start && t < i.end);
    if (this.insertEl) {
      this.insertEl.hidden = !insert;
      if (insert) this.insertEl.textContent = insert.kind === "card" ? insert.text : `B-roll: ${insert.query || insert.text}`;
    }
    this.syncMusic(t);
    this.syncSfx(t);
  }

  // ----- music bed (mirrors app/render.py _music_chain) -----
  musicGain(t) {
    const m = this.plan.music, d = this.plan.duration, fade = Math.min(0.5, d / 4), from = d - m.riser_s;
    let gain = m.riser_s > 0 && t >= from ? m.volume + (m.peak - m.volume) * (t - from) / m.riser_s : m.volume;
    gain *= Math.min(1, t / fade, (d - t) / fade);
    return Math.min(1, Math.max(0, gain));
  }

  syncMusic(t) {
    const m = this.plan.music;
    if (!m) { this.music?.pause(); return; }
    const src = `/api/music/${encodeURIComponent(m.file)}`;
    if (!this.music || this.music.dataset.src !== src) {
      this.music?.pause();
      this.music = new Audio(src); this.music.loop = true; this.music.dataset.src = src;
    }
    if (!this.playing) { this.music.pause(); return; }
    this.music.volume = this.musicGain(t);
    const length = this.music.duration;
    if (length && Math.abs(this.music.currentTime - t % length) > 0.25) this.music.currentTime = t % length;
    if (this.music.paused) this.music.play().catch(() => {});
  }

  // ----- sound effects (mirrors app/render.py _sfx_chain) -----
  syncSfx(t) {
    const last = this.sfxTime ?? t;
    this.sfxTime = t;
    if (!this.playing || t < last || t - last > 0.5) return;  // a seek or a pause: nothing fires
    for (const e of this.plan.sfx || []) {
      if (e.t > last && e.t <= t) {
        const sound = new Audio(`/api/sfx/${encodeURIComponent(e.file)}`);
        sound.volume = Math.min(1, e.gain);
        sound.play().catch(() => {});
      }
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
