// One video: progress while it is analysed, then the replay graph and its clips.

import { api, clock, el, showAlert } from "./util.js";

const POLL_MS = 1500;
const SVG_NS = "http://www.w3.org/2000/svg";
const GRAPH = { width: 1000, height: 200, pad: 10 };
const AXIS_STEPS = 4;
const REPLAY_BAR_FULL = 3;
let timer = null;
let current = null;

const MAX_HITS = 20;
const CLIP_TARGET_S = 35;
const CLIP_MAX_S = 60;
const LEAD_PAUSE_S = 0.7;
let words = null;

export function wireVideo() {
  el("search-form").addEventListener("submit", search);
  el("search-hits").addEventListener("click", makeClipFromHit);
  el("zip-export").addEventListener("click", async () => {
    try {
      const { job } = await api(`/api/videos/${current}/export-all`, "POST");
      followZip(job, current);
    } catch (error) { showAlert(error.message); }
  });
  el("video-retry").addEventListener("click", async () => {
    try { await api(`/api/videos/${current}/retry`, "POST"); await showVideo(current); } catch (error) { showAlert(error.message); }
  });
}

export function leaveVideo() { clearTimeout(timer); current = null; words = null; }

async function search(event) {
  event.preventDefault();
  const query = el("search-input").value.trim();
  if (query.length < 2) return;
  const id = current;
  el("search-hits").replaceChildren();
  if (event.submitter?.value === "ask") {
    try {
      const { job } = await api(`/api/videos/${id}/find`, "POST", { query });
      el("search-state").textContent = "Claude is looking for that moment…";
      for (;;) {
        const j = await api(`/api/jobs/${job}`);
        if (current !== id) return;
        if (j.status === "done") {
          el("search-state").textContent = `Added ${j.result.added.length} clip${j.result.added.length === 1 ? "" : "s"} at the end of the list.`;
          await showVideo(id); return;
        }
        if (j.status === "error") { el("search-state").textContent = ""; showAlert(j.error); return; }
        await new Promise((r) => setTimeout(r, 1500));
      }
    } catch (error) { showAlert(error.message); }
    return;
  }
  words = words || await api(`/api/videos/${id}/words`);
  const needle = query.toLowerCase().split(/\s+/);
  const hits = [];
  for (let i = 0; i < words.length && hits.length < MAX_HITS; i++) {
    const match = needle.every((n, k) => words[i + k] && (words[i + k].display.toLowerCase().includes(n) || words[i + k].text.toLowerCase().includes(n)));
    if (match) { hits.push(i); i += needle.length; }
  }
  el("search-state").textContent = hits.length ? `${hits.length}${hits.length === MAX_HITS ? "+" : ""} matches` : "No matches in the transcript.";
  el("search-hits").replaceChildren(...hits.map((i) => hitRow(i, needle.length)));
}

function hitRow(i, len) {
  const row = document.createElement("li");
  row.className = "search-hit";
  const time = document.createElement("span");
  time.className = "timecode"; time.textContent = clock(words[i].start);
  const text = document.createElement("span");
  const from = Math.max(0, i - 8), to = Math.min(words.length, i + len + 8);
  for (let k = from; k < to; k++) {
    const part = k >= i && k < i + len ? document.createElement("mark") : document.createElement("span");
    part.textContent = words[k].display + " ";
    text.append(part);
  }
  const button = document.createElement("button");
  button.type = "button"; button.className = "quiet-button"; button.textContent = "Make clip"; button.dataset.word = i;
  row.append(time, text, button);
  return row;
}

async function makeClipFromHit(event) {
  const button = event.target.closest("[data-word]");
  if (!button) return;
  const hit = Number(button.dataset.word);
  let first = hit;  // start at the pause before the hit, at most 15 words back
  while (first > 0 && hit - first < 15 && words[first].start - words[first - 1].end < LEAD_PAUSE_S) first -= 1;
  let last = hit;
  while (last + 1 < words.length && words[last + 1].end - words[first].start <= CLIP_MAX_S
         && words[last].end - words[first].start < CLIP_TARGET_S) last += 1;
  button.disabled = true;
  try {
    const { index } = await api(`/api/videos/${current}/clips`, "POST", { first_word: first, last_word: last });
    location.hash = `#/clip/${current}/${index}`;
  } catch (error) { showAlert(error.message); button.disabled = false; }
}

export async function showVideo(id) {
  clearTimeout(timer);
  current = id;
  let video;
  try { video = await api(`/api/videos/${id}`); } catch (error) { showAlert(error.message); return; }
  if (current !== id) return;
  document.title = `${video.title} · Clipper`;
  el("video-title").textContent = video.title;
  el("video-meta").textContent = [video.channel, video.duration ? clock(video.duration) : "", video.shape].filter(Boolean).join(" · ");

  const busy = Boolean(video.job) || !["ready", "failed"].includes(video.stage);
  el("video-progress").hidden = !(busy || video.stage === "failed");
  el("video-retry").hidden = video.stage !== "failed" || busy;
  el("video-stage").textContent = video.stage === "failed" ? `Stopped: ${video.error || "unknown error"}` : `${video.stage}…`;
  if (video.job) {
    try {
      const job = await api(`/api/jobs/${video.job}`);
      el("video-log").textContent = job.log.join("\n");
      el("video-log").scrollTop = el("video-log").scrollHeight;
    } catch { /* the job may have just finished */ }
  } else if (video.stage === "failed") {
    el("video-log").textContent = "";
  }

  el("video-graph-wrap").hidden = !video.clips.length;
  if (video.clips.length) drawGraph(video);
  el("clip-list").replaceChildren(...video.clips.map((c) => clipRow(video, c)));
  el("zip-bar").hidden = !video.clips.length || busy;
  el("search-form").hidden = video.stage !== "ready";
  el("zip-download").hidden = !video.zip_ready;
  el("zip-download").href = `/api/videos/${id}/clips.zip`;
  if (video.zip_job) followZip(video.zip_job, id);
  if (busy) timer = setTimeout(() => showVideo(id), POLL_MS);
}

let zipFollowing = null;

async function followZip(jobId, id) {
  if (zipFollowing === jobId) return;
  zipFollowing = jobId;
  el("zip-export").disabled = true;
  for (;;) {
    let job;
    try { job = await api(`/api/jobs/${jobId}`); } catch (error) { showAlert(error.message); break; }
    if (current !== id) break;
    el("zip-state").textContent = job.status === "queued" ? "Waiting for other exports…" : (job.log.at(-1) || "Exporting…");
    if (job.status === "done") { el("zip-state").textContent = "Zip ready."; el("zip-download").hidden = false; break; }
    if (job.status === "error") { el("zip-state").textContent = ""; showAlert(job.error); break; }
    await new Promise((r) => setTimeout(r, 1500));
  }
  el("zip-export").disabled = false;
  zipFollowing = null;
}

function svg(name, attrs) {
  const node = document.createElementNS(SVG_NS, name);
  Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, v));
  return node;
}

function drawGraph(video) {
  const length = Math.max(video.duration || 0, ...video.clips.map((c) => c.end), 1);
  const parts = [];
  for (let i = 1; i < AXIS_STEPS; i++) {
    const x = (GRAPH.width * i) / AXIS_STEPS;
    parts.push(svg("line", { class: "graph-grid", x1: x, y1: 0, x2: x, y2: GRAPH.height }));
  }
  if (video.heatmap.length) {
    const usable = GRAPH.height - GRAPH.pad * 2;
    const pts = video.heatmap.map((p) => `${(((p.start_time + p.end_time) / 2) / length * GRAPH.width).toFixed(1)},${(GRAPH.pad + (1 - p.value) * usable).toFixed(1)}`);
    const line = `M${pts.join(" L")}`;
    parts.push(svg("path", { class: "graph-area", d: `${line} L${GRAPH.width},${GRAPH.height} L0,${GRAPH.height} Z` }));
    parts.push(svg("path", { class: "graph-line", d: line }));
  } else {
    parts.push(svg("line", { class: "graph-empty", x1: 0, y1: GRAPH.height / 2, x2: GRAPH.width, y2: GRAPH.height / 2 }));
  }
  video.clips.forEach((c) => {
    const x = (c.start / length) * GRAPH.width, w = Math.max(((c.end - c.start) / length) * GRAPH.width, 3);
    const band = svg("rect", { class: "graph-band", x: x.toFixed(1), y: 0, width: w.toFixed(1), height: GRAPH.height });
    const title = document.createElementNS(SVG_NS, "title");
    title.textContent = `${clock(c.start)} · ${c.title}`;
    band.append(title);
    parts.push(band);
  });
  el("video-graph").replaceChildren(...parts);
  el("video-graph-axis").replaceChildren(...Array.from({ length: AXIS_STEPS + 1 }, (_, i) => {
    const tick = document.createElement("span"); tick.textContent = clock((length * i) / AXIS_STEPS); return tick;
  }));
  el("video-graph-caption").textContent = video.heatmap.length
    ? "Grey: how often each part of the video is replayed. Yellow: the clips found."
    : "YouTube has no replay graph for this video, so clips were picked from the transcript alone.";
}

function clipRow(video, clip) {
  const node = el("clip-row").content.firstElementChild.cloneNode(true);
  node.querySelector(".clip-link").href = `#/clip/${video.id}/${clip.index}`;
  node.querySelector(".clip-time").textContent = `${clock(clip.start)} – ${clock(clip.end)}`;
  node.querySelector(".clip-title").textContent = clip.title;
  node.querySelector(".clip-hook").textContent = clip.hook;
  const replay = node.querySelector(".replay");
  if (clip.retention === null || clip.retention === undefined) replay.hidden = true;
  else {
    replay.querySelector(".replay-bar span").style.transform = `scaleX(${Math.min(clip.retention / REPLAY_BAR_FULL, 1)})`;
    replay.querySelector(".replay-label").textContent = `${clip.retention.toFixed(1)}× replay`;
  }
  node.querySelector(".clip-status").textContent = clip.exported ? "Exported" : `Score ${clip.score}/10`;
  if (clip.status === "discarded") node.classList.add("is-discarded");
  return node;
}
