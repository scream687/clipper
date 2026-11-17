// One video: progress while it is analysed, then the replay graph and its clips.

import { api, clock, el, showAlert } from "./util.js";

const POLL_MS = 1500;
const SVG_NS = "http://www.w3.org/2000/svg";
const GRAPH = { width: 1000, height: 200, pad: 10 };
const AXIS_STEPS = 4;
const REPLAY_BAR_FULL = 3;
let timer = null;
let current = null;

export function wireVideo() {
  el("video-retry").addEventListener("click", async () => {
    try { await api(`/api/videos/${current}/retry`, "POST"); await showVideo(current); } catch (error) { showAlert(error.message); }
  });
}

export function leaveVideo() { clearTimeout(timer); current = null; }

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
  if (busy) timer = setTimeout(() => showVideo(id), POLL_MS);
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
