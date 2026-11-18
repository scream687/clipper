// Clip editor: transcript editing, look options, crop dragging and export.
// Every change is saved to the server, which answers with a new plan for the preview.

import { api, clock, copyText, el, showAlert, wait } from "./util.js";
import { Preview } from "./preview.js";

const CONTEXT_WORDS = 80;     // transcript words shown outside the clip on each side
const SAVE_DELAY_MS = 350;
const KEY_MERGE_S = 0.4;      // a new manual crop key replaces keys this close to it
const DRAG_MIN_PX = 4;        // pointer travel before a press counts as dragging the crop
const POLL_MS = 1000;

let state = null;             // { videoId, index, words, clip, saved, plan, sel: {a, b} | null }
let preview = null;
let saveTimer = null;
let editVersion = 0;          // bumped by every local edit; a save reply only replaces the clip if nothing changed since

function ensurePreview() {
  if (preview) return preview;
  preview = new Preview({
    stage: el("stage"), videos: [el("stage-a"), el("stage-b")], fill: el("stage-fill"), split: el("stage-split"), overlay: el("overlay"),
    onTime: (t) => updateTime(t),
    onEnd: () => { el("play").textContent = "Play"; },
  });
  wireControls();
  return preview;
}

export async function showEditor(videoId, index) {
  ensurePreview();
  showAlert("");
  el("editor-back").href = `#/video/${videoId}`;
  const [video, words, body, tracks, logo] = await Promise.all([
    api(`/api/videos/${videoId}`), api(`/api/videos/${videoId}/words`), api(`/api/videos/${videoId}/clips/${index}`),
    api("/api/music"), api("/api/logo"),
  ]);
  fillBrandChoices(tracks, logo, body.clip);
  preview.tweetUrl = `/api/videos/${videoId}/clips/${index}/tweet.png`;
  clearTimeout(saveTimer);
  state = { videoId, index, words, clip: body.clip, saved: body.clip, plan: body.plan, sel: null, video };
  el("export").disabled = false;
  document.title = `${body.clip.title || "Clip"} · Clipper`;
  el("editor-title").textContent = body.clip.title || "Untitled clip";
  preview.pause();
  if (!body.media.url) showAlert("The video for this clip is no longer on disk, so it cannot be previewed.");
  else preview.load(body.media.url, body.media.offset);
  preview.setPlan(body.plan);
  fillForm();
  renderTranscript();
  showExportState(body.exported, body.job);
  showDiscard();
}

// Tracks from the music library; a clip pointing at a deleted one keeps it listed so the choice is visible.
function fillBrandChoices(tracks, logo, clip) {
  const names = tracks.map((t) => t.name);
  if (clip.music && !names.includes(clip.music)) names.push(clip.music);
  el("look-form").elements.music.replaceChildren(new Option("None", ""), ...names.map((n) => new Option(n, n)));
  const toggle = el("look-form").elements.show_logo;
  toggle.disabled = !logo.has_logo;
  toggle.closest("label").title = logo.has_logo ? "" : "Add a logo in Settings first";
}

function showDiscard() {
  el("discard").textContent = state.clip.status === "discarded" ? "Restore clip" : "Discard clip";
}

// ---------- form ----------
function fillForm() {
  const f = el("look-form").elements, c = state.clip;
  ["look", "shape", "framing", ...CANVAS, ...EXTRAS, ...TEMPLATE].forEach((name) => { f[name].value = c[name]; });
  EXTRA_FLAGS.forEach((name) => { f[name].checked = c[name]; });
  showCanvasValues();
  ["show_hook", "show_handle", "show_credit", "swear_filter", "show_emoji"].forEach((name) => { f[name].checked = c[name]; });
  el("emoji-list").textContent = Object.values(c.emoji_words).join(" ");
  ["hook", "title", "description", "hashtags"].forEach((name) => { f[name].value = c[name]; });
  f.rating.value = c.rating ?? "";
}

const CANVAS = ["background", "bg_color", "bg_color2", "padding", "radius"];
const EXTRAS = ["music", "music_volume", "music_peak", "riser_s", "logo_corner", "logo_size"];  // numbers except music/corner
const EXTRA_FLAGS = ["show_logo", "show_follow", "tighten", "punch_in", "sfx", "sfx_safe_only", "show_progress", "show_broll"];
const TEMPLATE = ["template", "tweet_theme"];

function showCanvasValues() {
  const f = el("look-form").elements;
  f.padding_out.value = `${f.padding.value}%`;
  f.music_volume_out.value = `${f.music_volume.value}%`;
  f.music_peak_out.value = `${f.music_peak.value}%`;
  f.riser_s_out.value = `${f.riser_s.value} s`;
  f.logo_size_out.value = `${f.logo_size.value}%`;
  el("logo-controls").hidden = !f.show_logo.checked;
  f.tweet_theme.hidden = f.template.value !== "tweet";
  el("framing-controls").hidden = el("canvas-controls").hidden = f.template.value === "tweet";  // the post sets both
  f.radius_out.value = `${f.radius.value}px`;
  f.bg_color2.closest("label").hidden = f.background.value !== "gradient";
  f.bg_color.closest("label").hidden = f.background.value === "blur";
  f.bg_color.closest("label").firstElementChild.textContent = f.background.value === "gradient" ? "Top" : "Colour";
}

function readForm() {
  const f = el("look-form").elements;
  showCanvasValues();
  return {
    background: f.background.value, bg_color: f.bg_color.value.toUpperCase(), bg_color2: f.bg_color2.value.toUpperCase(),
    padding: Number(f.padding.value), radius: Number(f.radius.value),
    music: f.music.value, logo_corner: f.logo_corner.value, template: f.template.value, tweet_theme: f.tweet_theme.value,
    ...Object.fromEntries(["music_volume", "music_peak", "riser_s", "logo_size"].map((k) => [k, Number(f[k].value)])),
    ...Object.fromEntries(EXTRA_FLAGS.map((k) => [k, f[k].checked])),
    look: f.look.value, shape: f.shape.value, framing: f.framing.value,
    show_hook: f.show_hook.checked, show_handle: f.show_handle.checked, show_credit: f.show_credit.checked,
    swear_filter: f.swear_filter.checked, show_emoji: f.show_emoji.checked, hook: f.hook.value, title: f.title.value, description: f.description.value, hashtags: f.hashtags.value,
    rating: f.rating.value ? Number(f.rating.value) : null,
  };
}

function edit(changes, { now = false } = {}) {
  state.clip = { ...state.clip, ...changes };
  editVersion += 1;
  el("save-state").textContent = "Saving…";
  clearTimeout(saveTimer);
  saveTimer = setTimeout(saveQuietly, now ? 0 : SAVE_DELAY_MS);
  renderTranscript();
}

// Saves the current clip. Replies for another clip, or older than a later edit, are ignored, so a slow
// reply never overwrites newer typing. A rejected save puts back the last stored clip and throws.
async function save() {
  if (!state) return;
  const c = state.clip, { videoId, index } = state, version = editVersion;
  const payload = {
    first_word: c.first_word, last_word: c.last_word, cut_word_ids: c.cut_word_ids, text_fixes: c.text_fixes,
    look: c.look, shape: c.shape, framing: c.framing, ...Object.fromEntries([...CANVAS, ...EXTRAS, ...EXTRA_FLAGS, ...TEMPLATE].map((k) => [k, c[k]])), manual_keys: c.manual_keys, split_cam: c.split_cam ?? null, split_bottom: c.split_bottom ?? null, hook: c.hook,
    show_hook: c.show_hook, show_handle: c.show_handle, show_credit: c.show_credit, swear_filter: c.swear_filter,
    emoji_words: c.emoji_words, show_emoji: c.show_emoji,
    emphasis_words: c.emphasis_words ?? [], meme_word: c.meme_word ?? null, broll: c.broll ?? [],  // the AI's edit picks
    title: c.title, description: c.description, hashtags: c.hashtags, status: c.status, rating: c.rating ?? null,
  };
  const current = () => state && state.videoId === videoId && state.index === index;
  let body;
  try {
    body = await api(`/api/videos/${videoId}/clips/${index}`, "PUT", payload);
  } catch (error) {
    if (!current()) return;
    if (version === editVersion) {  // undo the rejected edit so later saves are not stuck repeating it
      state.clip = state.saved; fillForm(); renderTranscript(); showDiscard();
    }
    el("save-state").textContent = "Not saved";
    showAlert(error.message);
    throw error;
  }
  if (!current()) return;
  state.saved = body.clip;
  if (version !== editVersion) return;  // newer edits are waiting for their own save
  state.clip = body.clip; state.plan = body.plan;
  preview.setPlan(body.plan);
  el("save-state").textContent = "Saved";
  el("editor-title").textContent = body.clip.title || "Untitled clip";
  renderTranscript();
}

const saveQuietly = () => save().catch(() => {});  // the error is already shown

// ---------- transcript ----------
function renderTranscript() {
  const box = el("transcript");
  if (box.querySelector(".word-input")) return;  // a word is being corrected; it re-renders when that finishes
  const c = state.clip, words = state.words;
  const from = Math.max(0, c.first_word - CONTEXT_WORDS), to = Math.min(words.length - 1, c.last_word + CONTEXT_WORDS);
  const cut = new Set(c.cut_word_ids), fixes = c.text_fixes;
  const sel = state.sel ? [Math.min(state.sel.a, state.sel.b), Math.max(state.sel.a, state.sel.b)] : null;
  const nodes = [];
  for (let id = from; id <= to; id++) {
    const span = document.createElement("span");
    span.className = "w";
    span.dataset.id = id;
    const inside = id >= c.first_word && id <= c.last_word;
    if (!inside) span.classList.add("is-out");
    if (inside && cut.has(id)) span.classList.add("is-cut");
    if (fixes[id] !== undefined) span.classList.add("is-fixed");
    if (id === c.first_word || id === c.last_word) span.classList.add("is-edge");
    if (sel && id >= sel[0] && id <= sel[1]) span.classList.add("is-selected");
    span.textContent = fixes[id] ?? words[id].display;
    nodes.push(span, document.createTextNode(" "));
  }
  const keepScroll = box.scrollTop;
  box.replaceChildren(...nodes);
  box.scrollTop = keepScroll;
  el("clip-length").textContent = `${state.plan.duration.toFixed(1)} s`;
}

function scrollToClipStart() {
  // Scroll only the transcript box; scrollIntoView would also scroll the page.
  const box = el("transcript"), first = box.querySelector(`[data-id="${state?.clip.first_word}"]`);
  // Measured against the box itself: offsetTop counts from the nearest positioned ancestor, which overshoots on phones.
  if (!first) return;
  const lineH = parseFloat(getComputedStyle(box).lineHeight) || 32;
  const target = box.scrollTop + first.getBoundingClientRect().top - box.getBoundingClientRect().top - box.clientHeight / 3;
  box.scrollTop = Math.round(target / lineH) * lineH;  // whole lines, so no line is cut in half at the top
}

function selected() {
  if (!state.sel) return [];
  const a = Math.min(state.sel.a, state.sel.b), b = Math.max(state.sel.a, state.sel.b);
  return Array.from({ length: b - a + 1 }, (_, i) => a + i);
}

function onWordClick(event) {
  const span = event.target.closest(".w");
  if (!span) return;
  const id = Number(span.dataset.id);
  state.sel = event.shiftKey && state.sel ? { a: state.sel.a, b: id } : { a: id, b: id };
  renderTranscript();
  const word = state.plan.words.find((w) => w.word_id === id);
  if (word) preview.seek(word.start);
}

// The word being said at the playhead (I / O keys trim there, like an editor's in and out points).
function wordAtPlayhead() {
  const t = preview.time ?? 0;
  let id = null;
  for (const w of state.plan.words) { if (w.start <= t + 1e-3) id = w.word_id; else break; }
  return id ?? state.plan.words[0]?.word_id;
}

function setStart(at) {
  const ids = typeof at === "number" ? [at] : selected(); if (!ids.length) return;
  const first = ids[0];
  if (first > state.clip.last_word) return showAlert("The start must be before the clip's end.");
  edit({ first_word: first, cut_word_ids: state.clip.cut_word_ids.filter((i) => i >= first), ...keepInside(first, state.clip.last_word) });
}

function setEnd(at) {
  const ids = typeof at === "number" ? [at] : selected(); if (!ids.length) return;
  const last = ids[ids.length - 1];
  if (last < state.clip.first_word) return showAlert("The end must be after the clip's start.");
  edit({ last_word: last, cut_word_ids: state.clip.cut_word_ids.filter((i) => i <= last), ...keepInside(state.clip.first_word, last) });
}

// Word fixes and emojis outside the new edges are dropped (the server rejects them).
function keepInside(first, last) {
  const inside = (obj) => Object.fromEntries(Object.entries(obj).filter(([k]) => Number(k) >= first && Number(k) <= last));
  return { text_fixes: inside(state.clip.text_fixes), emoji_words: inside(state.clip.emoji_words) };
}

function toggleCut() {
  const ids = selected().filter((i) => i >= state.clip.first_word && i <= state.clip.last_word);
  if (!ids.length) return showAlert("Select words inside the clip to cut them.");
  const cut = new Set(state.clip.cut_word_ids);
  const allCut = ids.every((i) => cut.has(i));
  ids.forEach((i) => (allCut ? cut.delete(i) : cut.add(i)));
  const span = state.clip.last_word - state.clip.first_word + 1;
  if (cut.size >= span) return showAlert("A clip needs at least one word that is not cut.");
  edit({ cut_word_ids: [...cut].sort((a, b) => a - b) });
}

function fixWord() {
  const ids = selected();
  if (ids.length !== 1 || ids[0] < state.clip.first_word || ids[0] > state.clip.last_word) {
    return showAlert("Select one word inside the clip to correct it.");
  }
  const id = ids[0];
  const span = el("transcript").querySelector(`[data-id="${id}"]`);
  const input = document.createElement("input");
  input.className = "word-input";
  input.value = state.clip.text_fixes[id] ?? state.words[id].display;
  input.maxLength = 60;
  input.setAttribute("aria-label", "Corrected word");
  input.style.width = `${Math.max(6, input.value.length + 2)}ch`;
  span.replaceWith(input);
  input.focus(); input.select();
  const finish = (commit) => {
    input.removeEventListener("blur", onBlur);
    if (!commit) return renderTranscript();
    const value = input.value.trim(), fixes = { ...state.clip.text_fixes };
    if (!value || value === state.words[id].display) delete fixes[id]; else fixes[id] = value;
    edit({ text_fixes: fixes });
  };
  const onBlur = () => finish(true);
  input.addEventListener("blur", onBlur);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); finish(true); }
    if (e.key === "Escape") { e.preventDefault(); finish(false); }
  });
}

// ---------- crop dragging ----------
const WHEEL_ZOOM = 0.002;  // crop size change per wheel pixel (exponential, so in and out feel the same)
const CAM_MIN = 0.05;      // the facecam crop is never narrower than this share of the frame
const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi);
const camShare = ([x, y, w, h]) => {
  const p = state.plan, r = (v) => Math.round(v * 10000) / 10000;
  return [r(x / p.source_width), r(y / p.source_height), r(w / p.source_width), r(h / p.source_height)];
};
const onCam = (e) => e.offsetY < el("stage").clientHeight * state.plan.split.top_h / state.plan.height;

function wireCropDrag() {
  const stage = el("stage");
  let drag = null;
  stage.addEventListener("pointerdown", (e) => {
    if (!state || state.plan.framing === "fill") return;
    stage.setPointerCapture(e.pointerId);
    stage.classList.add("is-dragging");
    const p = state.plan;
    if (p.framing === "split") {  // each half has its own crop: drag the one under the pointer
      drag = { x: e.clientX, y: e.clientY, half: onCam(e) ? "top" : "bottom", start: preview.splitAt(onCam(e) ? "top" : "bottom") };
      return;
    }
    drag = { x: e.clientX, cx: preview.cropAt(preview.time ?? 0), span: p.crop_w / p.source_width };
  });
  stage.addEventListener("pointermove", (e) => {
    if (!drag || (!drag.moved && Math.hypot(e.clientX - drag.x, e.clientY - (drag.y ?? e.clientY)) < DRAG_MIN_PX)) return;
    drag.moved = true;  // a click (or Chrome's zero-distance pointermove) must not pin the crop
    const p = state.plan, dx = e.clientX - drag.x;
    if (drag.half === "top") {  // the facecam crop moves both ways, in source pixels
      const [x, y, w, h] = drag.start, topPx = stage.clientHeight * p.split.top_h / p.height;
      preview.setSplitOverride("top", [clamp(x - dx * w / stage.clientWidth, 0, p.source_width - w),
        clamp(y - (e.clientY - drag.y) * h / topPx, 0, p.source_height - h), w, h]);
      return;
    }
    const span = (drag.half ? p.split.crop_w : p.crop_w) / p.source_width;
    const cx = clamp((drag.half ? drag.start : drag.cx) - dx / stage.clientWidth * span, 0, 1);
    if (drag.half) { preview.setSplitOverride("bottom", cx); return; }
    preview.setCropOverride(cx);
  });
  // Scrolling over the facecam zooms its crop around the centre.
  stage.addEventListener("wheel", (e) => {
    if (!state || state.plan.framing !== "split" || !onCam(e)) return;
    e.preventDefault();
    const p = state.plan, [x, y, w, h] = preview.splitAt("top");
    const nw = Math.min(clamp(w * Math.exp(e.deltaY * WHEEL_ZOOM), p.source_width * CAM_MIN, p.source_width), p.source_height * w / h);
    const nh = nw * h / w;
    const rect = [clamp(x + (w - nw) / 2, 0, p.source_width - nw), clamp(y + (h - nh) / 2, 0, p.source_height - nh), nw, nh];
    preview.setSplitOverride("top", rect);
    edit({ split_cam: camShare(rect) });
  }, { passive: false });
  const end = () => {
    if (!drag) return;
    stage.classList.remove("is-dragging");
    if (drag.half) {
      const { half, moved } = drag;
      drag = null;
      if (!moved) return;
      const value = preview.splitAt(half);
      edit(half === "top" ? { split_cam: camShare(value) } : { split_bottom: Math.round(value * 10000) / 10000 }, { now: true });
      return;
    }
    const cx = preview.cropOverride;
    drag = null;
    if (cx === null || cx === undefined) return;
    const t = preview.sourceTime();
    const keys = state.clip.manual_keys.filter((k) => Math.abs(k.t - t) > KEY_MERGE_S);
    keys.push({ t, cx: Math.round(cx * 10000) / 10000, cy: 0.5, jump: true });
    keys.sort((a, b) => a.t - b.t);
    preview.setCropOverride(null);
    edit({ manual_keys: keys }, { now: true });
  };
  stage.addEventListener("pointerup", end);
  stage.addEventListener("pointercancel", () => {
    drag = null; stage.classList.remove("is-dragging"); preview.setCropOverride(null); preview.setSplitOverride(null);
  });
}

// ---------- time / export ----------
function updateTime(t) {
  if (!state) return;
  const scrub = el("scrub");
  scrub.max = String(state.plan.duration);
  if (document.activeElement !== scrub) scrub.value = String(t);
  el("time").textContent = `${clock(t)} / ${clock(state.plan.duration)}`;
  const playing = state.plan.words.find((w) => t >= w.start && t < w.end);
  const box = el("transcript");
  box.querySelector(".is-playing")?.classList.remove("is-playing");
  if (playing) box.querySelector(`[data-id="${playing.word_id}"]`)?.classList.add("is-playing");
}

function showExportState(exported, jobId) {
  const link = el("download");
  link.hidden = !exported;
  link.href = `/api/videos/${state.videoId}/clips/${state.index}/export.mp4?download=true`;
  el("export-state").textContent = exported ? "Exported. Edits since then need a new export." : "";
  if (jobId) followExport(jobId);
}

async function followExport(jobId) {
  const button = el("export");
  button.disabled = true;
  const { videoId, index } = state;
  for (;;) {
    let job;
    try { job = await api(`/api/jobs/${jobId}`); } catch (error) { showAlert(error.message); break; }
    if (!state || state.videoId !== videoId || state.index !== index) return;
    el("export-state").textContent = job.status === "queued" ? "Waiting for another export…" : (job.log.at(-1) || "Exporting…");
    if (job.status === "done") {  // the server marked it kept; keep that so the next save does not undo it
      state.clip = { ...state.clip, status: "kept" }; state.saved = { ...state.saved, status: "kept" };
      showExportState(true); break;
    }
    if (job.status === "error") { el("export-state").textContent = ""; showAlert(job.error); break; }
    await wait(POLL_MS);
  }
  button.disabled = false;
}

async function exportClip() {
  clearTimeout(saveTimer);
  try { await save(); } catch { return; }  // never export an older version than what the user sees
  try {
    const { job } = await api(`/api/videos/${state.videoId}/clips/${state.index}/export`, "POST");
    followExport(job);
  } catch (error) { showAlert(error.message); }
}

// ---------- wiring ----------
function wireControls() {
  el("play").addEventListener("click", async () => { await preview.toggle(); el("play").textContent = preview.playing ? "Pause" : "Play"; });
  el("scrub").addEventListener("input", (e) => preview.seek(Number(e.target.value)));
  el("transcript").addEventListener("click", onWordClick);
  el("tool-start").addEventListener("click", setStart);
  el("tool-end").addEventListener("click", setEnd);
  el("tool-cut").addEventListener("click", toggleCut);
  el("tool-fix").addEventListener("click", fixWord);
  el("reset-crop").addEventListener("click", () => edit({ manual_keys: [], split_cam: null, split_bottom: null }, { now: true }));
  el("look-form").addEventListener("input", (e) => {
    const instant = e.target.type === "radio" || e.target.type === "checkbox" || e.target.tagName === "SELECT";
    edit(readForm(), { now: instant });
  });
  el("export").addEventListener("click", exportClip);
  el("discard").addEventListener("click", () => {
    edit({ status: state.clip.status === "discarded" ? "new" : "discarded" }, { now: true });
    showDiscard();
  });
  el("copy-title").addEventListener("click", (e) => copyText(e.currentTarget, state.clip.title));
  el("copy-description").addEventListener("click", (e) => copyText(e.currentTarget, [state.clip.description, state.clip.hashtags].filter(Boolean).join("\n\n")));
  el("emoji-suggest").addEventListener("click", async (e) => {
    const button = e.currentTarget;
    button.disabled = true; button.textContent = "Asking the AI…";
    clearTimeout(saveTimer);
    try {
      await save();
      const body = await api(`/api/videos/${state.videoId}/clips/${state.index}/emojis`, "POST");
      state.clip = state.saved = body.clip; state.plan = body.plan; preview.setPlan(body.plan); fillForm();
    } catch (error) { showAlert(error.message); }
    button.disabled = false; button.textContent = "Suggest emojis";
  });
  el("look-save-default").addEventListener("click", () => lookAction("PUT", "/api/house-look", (r) => "New clips will start with this look."));
  el("look-apply-all").addEventListener("click", () => lookAction("POST", `/api/videos/${state.videoId}/apply-look`,
    (r) => `Applied to ${r.updated} other clip${r.updated === 1 ? "" : "s"}.`));
  el("emoji-clear").addEventListener("click", () => { edit({ emoji_words: {} }, { now: true }); el("emoji-list").textContent = ""; });
  wireCropDrag();
  wireTabs();
  document.addEventListener("keydown", (e) => {
    if (el("view-editor").hidden || !state) return;
    if (e.target.closest("input, textarea, select")) return;
    if ((e.key === " " || e.key === "Enter") && e.target.closest("button, a, summary")) return;  // let them press
    const actions = { " ": () => el("play").click(), "[": () => setStart(), "]": () => setEnd(), Delete: toggleCut, Backspace: toggleCut,
                      Enter: fixWord, i: () => setStart(wordAtPlayhead()), o: () => setEnd(wordAtPlayhead()) };
    const key = e.key.length === 1 ? e.key.toLowerCase() : e.key;
    if (actions[key] && !e.metaKey && !e.ctrlKey && !e.altKey) { e.preventDefault(); actions[key](); }
  });
}

// Style / Sound & extras / Post text: one form split into panes (arrow keys move between tabs).
function wireTabs() {
  const tabs = [...document.querySelectorAll(".tabs [role=tab]")];
  const pick = (tab) => tabs.forEach((t) => {
    const on = t === tab;
    t.setAttribute("aria-selected", String(on)); t.tabIndex = on ? 0 : -1;
    el(t.getAttribute("aria-controls")).hidden = !on;
  });
  tabs.forEach((tab, i) => {
    tab.addEventListener("click", () => pick(tab));
    tab.addEventListener("keydown", (e) => {
      const step = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
      if (!step) return;
      e.preventDefault();
      const next = tabs[(i + step + tabs.length) % tabs.length];
      pick(next); next.focus();
    });
  });
}

// Saves first, so the look sent is the one on screen.
async function lookAction(method, path, message) {
  clearTimeout(saveTimer);
  el("look-state").textContent = "";
  try {
    await save();
    const result = await api(path, method, { video_id: state.videoId, index: state.index });
    el("look-state").textContent = message(result);
  } catch (error) { showAlert(error.message); }
}

export function leaveEditor() {
  preview?.pause();
  if (el("play")) el("play").textContent = "Play";
}

export { scrollToClipStart };
