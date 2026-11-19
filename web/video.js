// One video: progress while it is analysed, then the review feed: a player and the clips to keep or drop.

import { api, clock, el, showAlert, streamTitle, youtubeId } from "./util.js";
import { Preview } from "./preview.js";

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
let shownId = null;          // the video whose search, zip and OpusClip messages are on screen
const MIN_QUERY = 3;         // the server's FindIn minimum

export function wireVideo() {
  el("search-form").addEventListener("submit", search);
  el("search-hits").addEventListener("click", makeClipFromHit);
  el("zip-export").addEventListener("click", async () => {
    try {
      const { job } = await api(`/api/videos/${current}/export-all`, "POST");
      followZip(job, current);
    } catch (error) { showAlert(error.message); }
  });
  el("opus-run").addEventListener("click", async () => {
    try {
      const { job } = await api(`/api/videos/${current}/opusclip`, "POST");
      followOpus(job, current);
    } catch (error) { showAlert(error.message); }
  });
  el("video-retry").addEventListener("click", async () => {
    try { await api(`/api/videos/${current}/retry`, "POST"); await showVideo(current); } catch (error) { showAlert(error.message); }
  });
  wireReview();
}

export function leaveVideo() {
  clearTimeout(timer); current = null; words = null;
  review.preview?.pause(); el("r-play").textContent = "Play";
}

// ---------- review feed ----------
const review = { clips: [], selected: null, preview: null, token: 0 };
const isOn = () => !el("view-video").hidden && !el("review").hidden;

function reviewPreview() {
  review.preview ||= new Preview({
    stage: el("r-stage"), videos: [el("r-a"), el("r-b")], fill: el("r-fill"), split: el("r-split"), overlay: el("r-overlay"),
    onTime: (t) => {
      const d = review.preview?.duration || 0;
      el("r-scrub").max = String(d);
      if (document.activeElement !== el("r-scrub")) el("r-scrub").value = String(t);
      el("r-time").textContent = `${clock(t)} / ${clock(d)}`;
    },
    onEnd: () => { el("r-play").textContent = "Play"; },
    maxVh: 50,  // small enough that keep and drop stay on screen beside it
  });
  return review.preview;
}

async function selectClip(index, { play = false } = {}) {
  const clip = review.clips.find((c) => c.index === index);
  if (!clip) return;
  review.selected = index;
  el("clip-list").querySelectorAll(".clip-row").forEach((row) => {
    const on = Number(row.dataset.index) === index;
    row.classList.toggle("is-selected", on);
    row.querySelector(".clip-pick").setAttribute("aria-current", on ? "true" : "false");
    if (on && isOn()) row.scrollIntoView({ block: "nearest" });
  });
  showCard(clip);
  const token = ++review.token, id = current;
  let body;
  try { body = await api(`/api/videos/${id}/clips/${index}`); } catch (error) { showAlert(error.message); return; }
  if (token !== review.token || id !== current) return;
  const preview = reviewPreview();
  preview.pause(); el("r-play").textContent = "Play";
  preview.tweetUrl = `/api/videos/${id}/clips/${index}/tweet.png`;
  if (body.media.url) preview.load(body.media.url, body.media.offset);
  preview.setPlan(body.plan);
  preview.seek(0);
  if (play) { await preview.toggle(); el("r-play").textContent = preview.playing ? "Pause" : "Play"; }
}

function showCard(clip) {
  const n = review.clips.indexOf(clip) + 1;
  const picked = clip.source === "opusclip" ? "Picked by OpusClip" : `Score ${clip.score}/10`;
  el("r-count").textContent = `Clip ${n} of ${review.clips.length} · ${clock(clip.end - clip.start)} · ${picked}`;
  el("r-edit").href = `#/clip/${current}/${clip.index}`;
  for (const [id, status] of [["r-keep", "kept"], ["r-drop", "discarded"]]) {
    el(id).classList.toggle("is-on", clip.status === status);
    el(id).setAttribute("aria-pressed", String(clip.status === status));
  }
}

function step(delta) {
  const at = review.clips.findIndex((c) => c.index === review.selected);
  const next = review.clips[Math.min(Math.max(at + delta, 0), review.clips.length - 1)];
  if (next) selectClip(next.index);
}

async function decide(status) {
  const clip = review.clips.find((c) => c.index === review.selected);
  if (!clip) return;
  const value = clip.status === status ? "new" : status;  // pressing it again undoes it
  try { await api(`/api/videos/${current}/clips/${clip.index}/status`, "POST", { status: value }); } catch (error) { showAlert(error.message); return; }
  clip.status = value;
  const row = el("clip-list").querySelector(`[data-index="${clip.index}"]`);
  if (row) paintState(row, clip);
  showTally();
  if (value === "new") { showCard(clip); return; }
  const at = review.clips.indexOf(clip);  // on to the next clip still waiting for a decision
  const next = review.clips.slice(at + 1).find((c) => c.status === "new") || review.clips[at + 1];
  if (next) selectClip(next.index, { play: true }); else showCard(clip);
}

function showTally() {
  const kept = review.clips.filter((c) => c.status === "kept").length;
  const dropped = review.clips.filter((c) => c.status === "discarded").length;
  const left = review.clips.length - kept - dropped;
  el("review-tally").textContent = `${review.clips.length} clips · ${kept} kept · ${dropped} dropped${left ? ` · ${left} to review` : " · all reviewed"}`;
  el("zip-export").textContent = kept ? `Export ${kept} kept clip${kept === 1 ? "" : "s"}` : `Export all ${review.clips.length - dropped} as zip`;
  el("zip-export").className = "quiet-button";  // Keep is the one primary until the review is done; then Next, or this
  el("review-next").hidden = true;
  if (!left) showNext(current);
}

// All decided: the next video with clips to review, the rest of this stream first, so a night of booms is one queue.
async function showNext(videoId) {
  let videos;
  try { videos = await api("/api/videos"); } catch { return; }
  if (videoId !== current) return;  // moved on while the list loaded
  const here = videos.find((v) => v.id === videoId), stream = here && youtubeId(here.url);
  const open = videos.filter((v) => v.id !== videoId && v.clips.some((c) => c.status === "new"));
  const next = open.find((v) => stream && youtubeId(v.url) === stream) || open[0];
  const link = el("review-next");
  link.hidden = !next;
  el("zip-export").className = next ? "quiet-button" : "solid-button";
  if (!next) return;
  const left = next.clips.filter((c) => c.status === "new").length;
  link.href = `#/video/${next.id}`;
  link.textContent = `Next: ${left} to review in ${streamTitle(next.title)}`;
}

function wireReview() {
  el("clip-list").addEventListener("click", (e) => {
    const row = e.target.closest(".clip-row");
    if (row) selectClip(Number(row.dataset.index));
  });
  el("clip-list").addEventListener("dblclick", (e) => {
    const row = e.target.closest(".clip-row");
    if (row) location.hash = `#/clip/${current}/${row.dataset.index}`;
  });
  el("r-keep").addEventListener("click", () => decide("kept"));
  el("r-drop").addEventListener("click", () => decide("discarded"));
  el("r-play").addEventListener("click", async () => {
    const p = reviewPreview();
    await p.toggle(); el("r-play").textContent = p.playing ? "Pause" : "Play";
  });
  el("r-scrub").addEventListener("input", (e) => review.preview?.seek(Number(e.target.value)));
  document.addEventListener("keydown", (e) => {
    if (!isOn() || e.metaKey || e.ctrlKey || e.altKey || e.target.closest("input, textarea, select")) return;
    if ((e.key === " " || e.key === "Enter") && e.target.closest("button, a, summary")) return;
    const actions = { j: () => step(1), k: () => step(-1), y: () => decide("kept"), n: () => decide("discarded"),
                      e: () => { if (review.selected !== null) location.hash = el("r-edit").getAttribute("href"); },
                      " ": () => el("r-play").click(), ArrowDown: () => step(1), ArrowUp: () => step(-1) };
    const key = e.key.length === 1 ? e.key.toLowerCase() : e.key;
    if (actions[key]) { e.preventDefault(); actions[key](); }
  });
}

async function search(event) {
  event.preventDefault();
  const query = el("search-input").value.trim();
  if (query.length < MIN_QUERY) { el("search-state").textContent = `Type at least ${MIN_QUERY} letters.`; return; }
  const id = current;
  el("search-hits").replaceChildren();
  if (event.submitter?.value === "ask") {
    try {
      const { job } = await api(`/api/videos/${id}/find`, "POST", { query });
      el("search-state").textContent = "The AI is looking for that moment…";
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
  words = words || await api(`/api/videos/${current}/words`);
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
  if (shownId !== id) {  // messages from the previous video must not carry over
    shownId = id; words = null; review.selected = null;
    review.preview?.pause();
    ["search-hits"].forEach((name) => el(name).replaceChildren());
    ["search-state", "zip-state", "opus-state"].forEach((name) => { el(name).textContent = ""; });
  }
  let video;
  try { video = await api(`/api/videos/${id}`); } catch (error) {
    showAlert(error.message);
    if (current === id) timer = setTimeout(() => showVideo(id), POLL_MS * 2);  // keep following through a server restart
    return;
  }
  if (current !== id) return;
  showAlert("");
  document.title = `${video.title} · Clipper`;
  el("video-title").textContent = video.title;
  el("video-meta").textContent = [video.channel, video.duration ? clock(video.duration) : "", video.shape].filter(Boolean).join(" · ");

  const busy = Boolean(video.job);
  const stuck = !busy && !["ready", "failed"].includes(video.stage);  // e.g. an upload that broke off
  el("video-progress").hidden = !(busy || stuck || video.stage === "failed");
  el("video-retry").hidden = busy || !(stuck || video.stage === "failed");
  el("video-stage").textContent = video.stage === "failed" ? `Stopped: ${video.error || "unknown error"}`
    : stuck ? "Stopped before it finished. Try again to restart it." : `${video.stage}…`;
  if (video.job) {
    try {
      const job = await api(`/api/jobs/${video.job}`);
      el("video-log").textContent = job.log.join("\n");
      el("video-log").scrollTop = el("video-log").scrollHeight;
    } catch { /* the job may have just finished */ }
  } else if (video.stage === "failed") {
    el("video-log").textContent = "";
  }

  el("review").hidden = !video.clips.length;
  el("video-graph-wrap").hidden = !video.clips.length;
  if (video.clips.length) drawGraph(video);
  review.clips = video.clips;
  el("clip-list").replaceChildren(...video.clips.map((c) => clipRow(video, c)));
  showTally();
  if (video.clips.length && !video.clips.some((c) => c.index === review.selected)) selectClip(video.clips[0].index);
  el("zip-bar").hidden = !video.clips.length || busy;
  el("search-form").hidden = video.stage !== "ready";
  el("zip-download").hidden = !video.zip_ready;
  el("zip-download").href = `/api/videos/${id}/clips.zip`;
  if (video.zip_job) followZip(video.zip_job, id);
  el("opus-run").hidden = !/^https:\/\/(www\.|m\.)?(youtube\.com|youtu\.be)\//.test(video.url) || video.opus_done;
  if (video.opus_job) followOpus(video.opus_job, id);
  else if (!opusFollowing) el("opus-state").textContent = "";
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

let opusFollowing = null;

async function followOpus(jobId, id) {
  if (opusFollowing === jobId) return;
  opusFollowing = jobId;
  el("opus-run").disabled = true;
  for (;;) {
    let job;
    try { job = await api(`/api/jobs/${jobId}`); } catch (error) { showAlert(error.message); break; }
    if (current !== id) break;
    el("opus-state").textContent = job.log.at(-1) || "Sending to OpusClip…";
    if (job.status === "done") {
      const n = job.result.added.length;
      opusFollowing = null; await showVideo(id);
      el("opus-state").textContent = `Added ${n} OpusClip clip${n === 1 ? "" : "s"} at the end of the list.`;
      return;
    }
    if (job.status === "error") { el("opus-state").textContent = ""; showAlert(job.error); break; }
    await new Promise((r) => setTimeout(r, 3000));
  }
  el("opus-run").disabled = false;
  opusFollowing = null;
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

function paintState(node, clip) {
  const label = { kept: "Kept", discarded: "Dropped" }[clip.status] || "";
  node.querySelector(".clip-state").textContent = label;
  node.classList.toggle("is-kept", clip.status === "kept");
  node.classList.toggle("is-discarded", clip.status === "discarded");
}

function clipRow(video, clip) {
  const node = el("clip-row").content.firstElementChild.cloneNode(true);
  node.dataset.index = clip.index;
  node.querySelector(".clip-poster").src = `/api/videos/${video.id}/clips/${clip.index}/poster.jpg`;
  node.querySelector(".clip-time").textContent = `${clock(clip.start)} · ${clock(clip.end - clip.start)}`;
  node.querySelector(".clip-title").textContent = clip.title;
  node.querySelector(".clip-hook").textContent = clip.hook;
  const replay = node.querySelector(".replay");
  if (clip.retention === null || clip.retention === undefined) replay.hidden = true;
  else {
    replay.querySelector(".replay-bar span").style.transform = `scaleX(${Math.min(clip.retention / REPLAY_BAR_FULL, 1)})`;
    replay.querySelector(".replay-label").textContent = `${clip.retention.toFixed(1)}× replay`;
  }
  const said = clip.mentions ? ` · ${clip.mentions} comment${clip.mentions === 1 ? "" : "s"} point here` : "";
  const picked = clip.source === "opusclip" ? "Picked by OpusClip" : `Score ${clip.score}/10`;
  node.querySelector(".clip-status").textContent = (clip.exported ? "Exported" : picked) + said;
  paintState(node, clip);
  if (clip.index === review.selected) node.classList.add("is-selected");
  return node;
}
