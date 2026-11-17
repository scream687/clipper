// Clip editor: transcript editing, look options, crop dragging and export.
// Every change is saved to the server, which answers with a new plan for the preview.

import { api, clock, copyText, el, showAlert, wait } from "./util.js";
import { Preview } from "./preview.js";

const CONTEXT_WORDS = 80;     // transcript words shown outside the clip on each side
const SAVE_DELAY_MS = 350;
const KEY_MERGE_S = 0.4;      // a new manual crop key replaces keys this close to it
const POLL_MS = 1000;

let state = null;             // { videoId, index, words, clip, plan, sel: {a, b} | null }
let preview = null;
let saveTimer = null;

function ensurePreview() {
  if (preview) return preview;
  preview = new Preview({
    stage: el("stage"), videos: [el("stage-a"), el("stage-b")], fill: el("stage-fill"), overlay: el("overlay"),
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
  const [video, words, body] = await Promise.all([
    api(`/api/videos/${videoId}`), api(`/api/videos/${videoId}/words`), api(`/api/videos/${videoId}/clips/${index}`),
  ]);
  state = { videoId, index, words, clip: body.clip, plan: body.plan, sel: null, video };
  document.title = `${body.clip.title || "Clip"} · Clipper`;
  el("editor-title").textContent = body.clip.title || "Untitled clip";
  preview.pause();
  preview.load(`/api/videos/${videoId}/proxy.mp4`);
  preview.setPlan(body.plan);
  fillForm();
  renderTranscript();
  showExportState(body.exported, body.job);
}

// ---------- form ----------
function fillForm() {
  const f = el("look-form").elements, c = state.clip;
  ["look", "shape", "framing"].forEach((name) => { f[name].value = c[name]; });
  ["show_hook", "show_handle", "show_credit", "swear_filter"].forEach((name) => { f[name].checked = c[name]; });
  ["hook", "title", "description"].forEach((name) => { f[name].value = c[name]; });
}

function readForm() {
  const f = el("look-form").elements;
  return {
    look: f.look.value, shape: f.shape.value, framing: f.framing.value,
    show_hook: f.show_hook.checked, show_handle: f.show_handle.checked, show_credit: f.show_credit.checked,
    swear_filter: f.swear_filter.checked, hook: f.hook.value, title: f.title.value, description: f.description.value,
  };
}

function edit(changes, { now = false } = {}) {
  state.clip = { ...state.clip, ...changes };
  el("save-state").textContent = "Saving…";
  clearTimeout(saveTimer);
  saveTimer = setTimeout(save, now ? 0 : SAVE_DELAY_MS);
  renderTranscript();
}

async function save() {
  const c = state.clip;
  const payload = {
    first_word: c.first_word, last_word: c.last_word, cut_word_ids: c.cut_word_ids, text_fixes: c.text_fixes,
    look: c.look, shape: c.shape, framing: c.framing, manual_keys: c.manual_keys, hook: c.hook,
    show_hook: c.show_hook, show_handle: c.show_handle, show_credit: c.show_credit, swear_filter: c.swear_filter,
    title: c.title, description: c.description, status: c.status,
  };
  try {
    const body = await api(`/api/videos/${state.videoId}/clips/${state.index}`, "PUT", payload);
    state.clip = body.clip; state.plan = body.plan;
    preview.setPlan(body.plan);
    el("save-state").textContent = "Saved";
    el("editor-title").textContent = body.clip.title || "Untitled clip";
    renderTranscript();
  } catch (error) {
    el("save-state").textContent = "";
    showAlert(error.message);
  }
}

// ---------- transcript ----------
function renderTranscript() {
  const c = state.clip, words = state.words;
  const from = Math.max(0, c.first_word - CONTEXT_WORDS), to = Math.min(words.length - 1, c.last_word + CONTEXT_WORDS);
  const cut = new Set(c.cut_word_ids), fixes = c.text_fixes;
  const sel = state.sel ? [Math.min(state.sel.a, state.sel.b), Math.max(state.sel.a, state.sel.b)] : null;
  const box = el("transcript");
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
  if (first) box.scrollTop = first.offsetTop - box.clientHeight / 3;
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

function setStart() {
  const ids = selected(); if (!ids.length) return;
  const first = ids[0];
  if (first > state.clip.last_word) return showAlert("The start must be before the clip's end.");
  edit({ first_word: first, cut_word_ids: state.clip.cut_word_ids.filter((i) => i >= first), text_fixes: keepFixes(first, state.clip.last_word) });
}

function setEnd() {
  const ids = selected(); if (!ids.length) return;
  const last = ids[ids.length - 1];
  if (last < state.clip.first_word) return showAlert("The end must be after the clip's start.");
  edit({ last_word: last, cut_word_ids: state.clip.cut_word_ids.filter((i) => i <= last), text_fixes: keepFixes(state.clip.first_word, last) });
}

function keepFixes(first, last) {
  return Object.fromEntries(Object.entries(state.clip.text_fixes).filter(([k]) => Number(k) >= first && Number(k) <= last));
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
function wireCropDrag() {
  const stage = el("stage");
  let drag = null;
  stage.addEventListener("pointerdown", (e) => {
    if (!state || state.plan.framing === "fill") return;
    stage.setPointerCapture(e.pointerId);
    stage.classList.add("is-dragging");
    drag = { x: e.clientX, cx: preview.cropAt(preview.time ?? 0) };
  });
  stage.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const p = state.plan, frac = (e.clientX - drag.x) / stage.clientWidth * (p.crop_w / p.source_width);
    preview.setCropOverride(Math.min(Math.max(drag.cx - frac, 0), 1));
  });
  const end = () => {
    if (!drag) return;
    stage.classList.remove("is-dragging");
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
  stage.addEventListener("pointercancel", () => { drag = null; stage.classList.remove("is-dragging"); preview.setCropOverride(null); });
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
    if (job.status === "done") { showExportState(true); break; }
    if (job.status === "error") { el("export-state").textContent = ""; showAlert(job.error); break; }
    await wait(POLL_MS);
  }
  button.disabled = false;
}

async function exportClip() {
  clearTimeout(saveTimer);
  await save();
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
  el("reset-crop").addEventListener("click", () => edit({ manual_keys: [] }, { now: true }));
  el("look-form").addEventListener("input", (e) => {
    const instant = e.target.type === "radio" || e.target.type === "checkbox";
    edit(readForm(), { now: instant });
  });
  el("export").addEventListener("click", exportClip);
  el("copy-title").addEventListener("click", (e) => copyText(e.currentTarget, state.clip.title));
  el("copy-description").addEventListener("click", (e) => copyText(e.currentTarget, state.clip.description));
  wireCropDrag();
  document.addEventListener("keydown", (e) => {
    if (el("view-editor").hidden || !state) return;
    if (e.target.closest("input, textarea, select")) return;
    const actions = { " ": () => el("play").click(), "[": setStart, "]": setEnd, Delete: toggleCut, Backspace: toggleCut, Enter: fixWord };
    if (actions[e.key] && !e.metaKey && !e.ctrlKey) { e.preventDefault(); actions[e.key](); }
  });
}

export function leaveEditor() {
  preview?.pause();
  if (el("play")) el("play").textContent = "Play";
}

export { scrollToClipStart };
