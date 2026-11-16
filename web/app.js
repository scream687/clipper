// Clipper page logic.
// Everything that comes from the server (video titles, AI-written text, log
// lines) is untrusted and only ever reaches the page through textContent.

const POLL_MS = 1000;
const COPIED_MS = 1500;
const SVG_NS = "http://www.w3.org/2000/svg";
const GRAPH = { width: 1000, height: 240, pad: 14 };
const REPLAY_BAR_FULL = 3; // a replay score of 3x the video average fills the bar
const AXIS_STEPS = 4;
const JOB_HEADINGS = {
  find: { running: "Finding moments", done: "Moments found", error: "Could not find moments" },
  render: { running: "Making clips", done: "Clips ready", error: "Could not make the clips" },
};

const el = (id) => document.getElementById(id);

let session = null; // the session view last loaded from the server
let picked = new Set(); // highlight indexes ticked for rendering
let busy = false;

// ---------- small helpers ----------

function clock(seconds) {
  const total = Math.max(0, Math.round(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const rest = String(total % 60).padStart(2, "0");
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${rest}` : `${minutes}:${rest}`;
}

function describeError(body, status) {
  const detail = body && body.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((item) => String(item.msg || "").replace(/^Value error, /, "")).join("\n");
  }
  return `The request failed (${status}).`;
}

async function api(path, method = "GET", payload) {
  let response;
  try {
    response = await fetch(path, {
      method,
      headers: payload ? { "content-type": "application/json" } : {},
      body: payload ? JSON.stringify(payload) : undefined,
    });
  } catch {
    throw new Error("Cannot reach the clipper server. Is run.sh still running?");
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(describeError(body, response.status));
  return body;
}

function showAlert(message) {
  el("alert").textContent = message;
  el("alert").hidden = false;
}

function clearAlert() {
  el("alert").hidden = true;
}

function setBusy(value) {
  busy = value;
  el("find-button").disabled = value;
  updateRenderButton();
}

function setHash(key, value) {
  history.replaceState(null, "", `#${key}=${encodeURIComponent(value)}`);
}

// ---------- settings ----------

function toggleSettings(open) {
  el("settings").hidden = !open;
  el("settings-toggle").setAttribute("aria-expanded", String(open));
}

function showSettings(settings) {
  const form = el("settings-form");
  form.elements.base_url.value = settings.base_url;
  form.elements.model.value = settings.model;
  form.elements.api_key.value = "";
  el("key-state").textContent = settings.api_key_set
    ? `saved, ends in ${settings.api_key_hint || "…"}`
    : "not saved yet";
  el("logo-toggle").disabled = !settings.logo_available;
}

async function loadSettings() {
  const settings = await api("/api/settings");
  showSettings(settings);
  if (!settings.api_key_set) toggleSettings(true);
}

async function saveSettings(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const status = el("settings-status");
  try {
    const saved = await api("/api/settings", "PUT", {
      base_url: form.elements.base_url.value.trim(),
      model: form.elements.model.value.trim(),
      api_key: form.elements.api_key.value.trim() || null,
    });
    showSettings(saved);
    status.textContent = "Saved.";
  } catch (error) {
    status.textContent = error.message;
  }
}

// ---------- jobs ----------

function showProgress(job, startedAt) {
  const headings = JOB_HEADINGS[job.kind] || JOB_HEADINGS.find;
  el("progress").hidden = false;
  el("progress-heading").textContent = headings[job.status] || headings.running;
  el("elapsed").textContent = clock((Date.now() - startedAt) / 1000);
  const log = el("log");
  log.textContent = job.log.join("\n");
  log.scrollTop = log.scrollHeight;
}

async function watchJob(jobId, startedAt = Date.now()) {
  let job;
  try {
    job = await api(`/api/jobs/${jobId}`);
  } catch (error) {
    setBusy(false);
    showAlert(error.message);
    return;
  }
  showProgress(job, startedAt);
  if (job.status === "running") {
    setTimeout(() => watchJob(jobId, startedAt), POLL_MS);
    return;
  }
  setBusy(false);
  if (job.status === "error") {
    showAlert(job.error || "The job failed. The log above has the details.");
    return;
  }
  await loadSession(job.result.session_id);
  el("progress").hidden = true;
  const target = job.kind === "render" ? el("clips") : el("moments");
  target.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function beginJob(path, payload) {
  clearAlert();
  setBusy(true);
  try {
    const { job_id: jobId } = await api(path, "POST", payload);
    setHash("job", jobId);
    await watchJob(jobId);
  } catch (error) {
    setBusy(false);
    showAlert(error.message);
  }
}

function findMoments(event) {
  event.preventDefault();
  const fields = event.currentTarget.elements;
  if (!fields.url.value.trim()) {
    showAlert("Paste a YouTube link first.");
    fields.url.focus();
    return;
  }
  el("moments").hidden = true;
  el("clips").hidden = true;
  beginJob("/api/highlights", {
    url: fields.url.value.trim(),
    num_clips: Number(fields.num_clips.value),
    min_seconds: Number(fields.min_seconds.value),
    max_seconds: Number(fields.max_seconds.value),
    direction: fields.direction.value.trim(),
    output_language: fields.output_language.value,
  });
}

function makeClips(event) {
  event.preventDefault();
  if (!session || picked.size === 0) return;
  const fields = event.currentTarget.elements;
  beginJob("/api/render", {
    session_id: session.session_id,
    indices: [...picked],
    reframe: fields.reframe.value,
    captions: fields.captions.checked,
    hook: fields.hook.checked,
    credit: fields.credit.checked,
    logo: fields.logo.checked,
  });
}

// ---------- session: graph, highlights, clips ----------

function videoLength() {
  const graphEnd = session.heatmap.length ? session.heatmap[session.heatmap.length - 1].end_time : 0;
  const lastPick = Math.max(0, ...session.highlights.map((h) => h.end_seconds));
  return Math.max(session.video.duration || 0, graphEnd, lastPick, 1);
}

function svgNode(name, attributes) {
  const node = document.createElementNS(SVG_NS, name);
  Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, value));
  return node;
}

function replayCurve(length) {
  const usable = GRAPH.height - GRAPH.pad * 2;
  const points = session.heatmap.map((p) => {
    const x = (((p.start_time + p.end_time) / 2) / length) * GRAPH.width;
    const y = GRAPH.pad + (1 - p.value) * usable;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const line = `M${points.join(" L")}`;
  const area = `${line} L${GRAPH.width},${GRAPH.height} L0,${GRAPH.height} Z`;
  return [
    svgNode("path", { class: "graph-area", d: area }),
    svgNode("path", { class: "graph-line", d: line }),
  ];
}

function pickBand(highlight, length) {
  const x = (highlight.start_seconds / length) * GRAPH.width;
  const width = Math.max(((highlight.end_seconds - highlight.start_seconds) / length) * GRAPH.width, 4);
  const band = svgNode("rect", {
    class: "graph-band", x: x.toFixed(1), y: 0, width: width.toFixed(1), height: GRAPH.height,
  });
  band.dataset.index = highlight.index;
  const label = document.createElementNS(SVG_NS, "title");
  label.textContent = `${clock(highlight.start_seconds)} · ${highlight.title}`;
  band.append(label);
  band.addEventListener("click", () => setPicked(highlight.index, !picked.has(highlight.index)));
  band.addEventListener("mouseenter", () => setActive(highlight.index));
  band.addEventListener("mouseleave", () => setActive(null));
  return band;
}

function drawGraph() {
  const length = videoLength();
  const hasGraph = session.heatmap.length > 0;
  const middle = GRAPH.height / 2;
  const base = hasGraph
    ? replayCurve(length)
    : [svgNode("line", { class: "graph-empty", x1: 0, y1: middle, x2: GRAPH.width, y2: middle })];
  el("graph").replaceChildren(...base, ...session.highlights.map((h) => pickBand(h, length)));

  const ticks = Array.from({ length: AXIS_STEPS + 1 }, (_, step) => {
    const tick = document.createElement("span");
    tick.textContent = clock((length * step) / AXIS_STEPS);
    return tick;
  });
  el("graph-axis").replaceChildren(...ticks);
  el("graph-caption").textContent = hasGraph
    ? "Grey curve: how often each part of the video gets replayed. Yellow bands: the moments picked. Click a band or tick a row to choose it."
    : "YouTube has no replay graph for this video, so these moments were picked from the transcript alone.";
}

function highlightRow(highlight) {
  const row = el("highlight-template").content.firstElementChild.cloneNode(true);
  row.dataset.index = highlight.index;
  const box = row.querySelector("input");
  box.addEventListener("change", () => setPicked(highlight.index, box.checked));
  row.querySelector(".visually-hidden").textContent = `Make this clip: ${highlight.title}`;
  row.querySelector(".timecode").textContent = `${clock(highlight.start_seconds)} – ${clock(highlight.end_seconds)}`;
  const seconds = Math.round(highlight.end_seconds - highlight.start_seconds);
  row.querySelector(".highlight-length").textContent = highlight.clip_url ? `${seconds}s · clip made` : `${seconds}s`;
  row.querySelector("h3").textContent = highlight.title;
  row.querySelector(".highlight-hook").textContent = highlight.hook_text;
  row.querySelector(".highlight-description").textContent = highlight.description;

  if (highlight.retention_score !== null && highlight.retention_score !== undefined) {
    const replay = row.querySelector(".replay");
    replay.hidden = false;
    const fill = Math.min(highlight.retention_score / REPLAY_BAR_FULL, 1);
    replay.querySelector(".replay-bar span").style.transform = `scaleX(${fill})`;
    replay.querySelector(".replay-label").textContent = `${highlight.retention_score.toFixed(1)}× replay`;
  }
  const link = row.querySelector("a");
  if (/^https:\/\//.test(highlight.watch_url)) link.href = highlight.watch_url;
  else link.hidden = true;

  row.addEventListener("mouseenter", () => setActive(highlight.index));
  row.addEventListener("mouseleave", () => setActive(null));
  return row;
}

function marked(index) {
  return document.querySelectorAll(`[data-index="${index}"]`);
}

function setActive(index) {
  document.querySelectorAll(".is-active").forEach((node) => node.classList.remove("is-active"));
  if (index !== null) marked(index).forEach((node) => node.classList.add("is-active"));
}

function setPicked(index, on) {
  const next = new Set(picked);
  if (on) next.add(index);
  else next.delete(index);
  picked = next;
  marked(index).forEach((node) => {
    node.classList.toggle("is-picked", on);
    const box = node.querySelector && node.querySelector("input[type=checkbox]");
    if (box) box.checked = on;
  });
  updateRenderButton();
}

function updateRenderButton() {
  const button = el("render-button");
  const count = picked.size;
  button.disabled = busy || count === 0;
  el("render-form").classList.toggle("has-picks", count > 0);
  button.textContent = count === 0 ? "Tick moments to make clips" : `Make ${count} clip${count === 1 ? "" : "s"}`;
}

async function copyText(button, text) {
  const original = button.textContent;
  try {
    await navigator.clipboard.writeText(text);
    button.textContent = "Copied";
  } catch {
    button.textContent = "Copy failed";
  }
  setTimeout(() => { button.textContent = original; }, COPIED_MS);
}

function clipCard(highlight, stamp) {
  const card = el("clip-template").content.firstElementChild.cloneNode(true);
  // The stamp stops the browser replaying a cached file after a clip is remade.
  card.querySelector("video").src = `${highlight.clip_url}?v=${stamp}`;
  card.querySelector("h3").textContent = highlight.title;
  card.querySelector(".clip-description").textContent = highlight.description;
  card.querySelectorAll("[data-copy]").forEach((button) => {
    button.addEventListener("click", () => copyText(button, highlight[button.dataset.copy] || ""));
  });
  const download = card.querySelector("a");
  download.href = `${highlight.clip_url}?download=true`;
  download.setAttribute("download", "");
  return card;
}

function showSession() {
  el("video-channel").textContent = session.video.channel;
  el("video-title").textContent = session.video.title;
  drawGraph();
  el("highlights").replaceChildren(...session.highlights.map(highlightRow));

  const made = session.highlights.filter((h) => h.clip_url);
  const stamp = Date.now();
  el("clip-strip").replaceChildren(...made.map((h) => clipCard(h, stamp)));
  el("clips").hidden = made.length === 0;
  el("moments").hidden = false;
  updateRenderButton();
}

async function loadSession(sessionId) {
  session = await api(`/api/sessions/${sessionId}`);
  picked = new Set();
  setHash("session", sessionId);
  if (!el("url").value) el("url").value = session.url;
  showSession();
}

// ---------- start ----------

async function resume() {
  const [key, value] = location.hash.slice(1).split("=");
  if (!value) return;
  try {
    if (key === "job") {
      setBusy(true);
      await watchJob(decodeURIComponent(value));
    } else if (key === "session") {
      await loadSession(decodeURIComponent(value));
    }
  } catch (error) {
    setBusy(false);
    showAlert(error.message);
  }
}

el("settings-toggle").addEventListener("click", () => toggleSettings(el("settings").hidden));
el("settings-form").addEventListener("submit", saveSettings);
el("find-form").addEventListener("submit", findMoments);
el("render-form").addEventListener("submit", makeClips);
updateRenderButton();
loadSettings().catch((error) => showAlert(error.message));
resume();
