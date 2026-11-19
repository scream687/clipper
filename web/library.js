// Library: add a video, list every video with its progress and its best clips as cards.

import { api, clock, el, showAlert, streamTitle, youtubeId } from "./util.js";

const POLL_MS = 2500;
const MAX_CARDS = 6;  // per row; the rest are one click away in the review feed
let timer = null;
const rendered = new Map();  // row key (its video ids) -> { sig, node }: unchanged rows keep their nodes, so posters don't flash on each poll

export function wireLibrary() {
  el("add-form").addEventListener("submit", addVideo);
  el("options-toggle").addEventListener("click", () => {
    const open = el("add-more").hidden;
    el("add-more").hidden = !open;
    el("options-toggle").setAttribute("aria-expanded", String(open));
  });
  el("add-file").addEventListener("change", showFile);
  const card = el("add-form");
  card.addEventListener("dragover", (e) => { if (e.dataTransfer.types.includes("Files")) { e.preventDefault(); card.classList.add("is-dropping"); } });
  card.addEventListener("dragleave", (e) => { if (!card.contains(e.relatedTarget)) card.classList.remove("is-dropping"); });
  card.addEventListener("drop", (e) => {
    card.classList.remove("is-dropping");
    if (!e.dataTransfer.files.length) return;
    e.preventDefault();
    el("add-file").files = e.dataTransfer.files;
    showFile();
  });
  el("video-list").addEventListener("click", deleteVideo);
  el("video-list").addEventListener("click", toggleWatch);
  el("watch-list").addEventListener("click", addWatched);
  el("trending-list").addEventListener("click", addWatched);
}

export async function showLibrary() {
  document.title = "Clipper";
  await refresh();
  loadChannels();
  loadTrending();
}

const compact = (n) => Intl.NumberFormat("en", { notation: "compact" }).format(n);
const when = (days) => (days > 1 ? `in ${days} days` : { 1: "tomorrow", 0: "today", [-1]: "yesterday" }[days] ?? `${-days} days ago`);
const age = (hours) => (hours < 24 ? `${hours} h ago` : `${Math.round(hours / 24)} d ago`);

async function loadTrending() {
  el("trending").hidden = false;
  el("trending-state").textContent = "Checking what India is watching…";
  let t;
  try { t = await api("/api/trends"); } catch (error) { el("trending-state").textContent = error.message; return; }
  const chip = (text, cls) => Object.assign(document.createElement("li"), { textContent: text, title: text, className: `trend-chip ${cls}` });
  el("trend-chips").replaceChildren(
    ...t.upcoming.map((e) => chip(`${e.terms[0]} · ${when(e.days)}`, "is-upcoming")),
    ...t.searches.map((s) => chip(`${s.term}${s.traffic ? ` · ${s.traffic}` : ""}`, "")));
  el("trending-list").replaceChildren(...t.videos.map(trendRow));
  el("trending-state").textContent = t.videos.length ? "Uploads beating their channel's usual views this week." :
    "No fresh creator uploads right now. Edit the creator list in Settings.";
}

function trendRow(video) {
  const node = watchRow({ channel: video.channel }, { ...video, views: 0, duration: 0 });
  node.querySelector(".video-sub").textContent = [video.channel, `${video.ratio}× usual`, `${compact(video.views)} views`, age(video.age_h)].join(" · ");
  return node;
}

async function loadChannels() {
  el("watch").hidden = false;
  el("watch-state").textContent = "Checking your channels…";
  let channels;
  try { channels = await api("/api/channels"); } catch (error) { el("watch-state").textContent = error.message; return; }
  if (!channels.length) { el("watch").hidden = true; return; }
  const broken = channels.filter((c) => c.error).map((c) => c.handle);
  el("watch-state").textContent = broken.length ? `Could not read ${broken.join(", ")}.` : "";
  el("watch-list").replaceChildren(...channels.flatMap((c) => c.videos.slice(0, 3).map((v) => watchRow(c, v))));
}

function watchRow(channel, video) {
  const node = el("watch-row").content.firstElementChild.cloneNode(true);
  node.querySelector(".video-link").href = video.url;
  node.querySelector(".video-name").textContent = video.title;
  const views = video.views ? `${Intl.NumberFormat("en", { notation: "compact" }).format(video.views)} views` : "";
  node.querySelector(".video-sub").textContent = [channel.channel, video.duration ? clock(video.duration) : "", views].filter(Boolean).join(" · ");
  const add = node.querySelector(".watch-add");
  add.dataset.url = video.url;
  add.disabled = video.added;
  if (video.added) add.textContent = "In library";
  return node;
}

async function addWatched(event) {
  const button = event.target.closest(".watch-add");
  if (!button || button.disabled) return;
  const f = el("add-form").elements;
  button.disabled = true; button.textContent = "Adding…";
  try {
    const { id } = await api("/api/videos", "POST", { url: button.dataset.url, shape: f.shape.value, max_clips: Number(f.max_clips.value),
      min_s: Number(f.min_s.value), max_s: Number(f.max_s.value), direction: f.direction.value.trim(), live_minutes: Number(f.live_minutes.value),
      watch_chat: f.watch_chat.checked });
    location.hash = `#/video/${id}`;
  } catch (error) { showAlert(error.message); button.disabled = false; button.textContent = "Find clips"; }
}

export function leaveLibrary() { clearTimeout(timer); }

function showFile() {
  const file = el("add-file").files[0];
  el("upload-label").textContent = file ? file.name : "Upload video";
  el("add-file").closest(".chip").classList.toggle("is-on", Boolean(file));
  if (file) el("add-url").value = "";
}

async function refresh() {
  clearTimeout(timer);
  let videos;
  try { videos = await api("/api/videos"); } catch (error) { showAlert(error.message); return; }
  const groups = byStream(videos);
  el("library-empty").hidden = videos.length > 0;
  el("side-count").textContent = groups.length || "";
  const nodes = groups.map((members) => {
    const key = members.map((m) => m.id).join(","), sig = JSON.stringify(members), seen = rendered.get(key);
    if (seen?.sig === sig) return seen.node;
    const node = row(members);
    rendered.set(key, { sig, node });
    return node;
  });
  el("video-list").replaceChildren(...nodes);
  if (videos.some((v) => v.job || v.watching)) timer = setTimeout(refresh, POLL_MS);
}


// Every capture of one live stream (each chat boom is its own video) shares the stream's link: one group per stream.
function byStream(videos) {
  const groups = new Map();
  for (const video of videos) {  // newest first, as the server lists them
    const key = (video.live && youtubeId(video.url)) || video.id;
    groups.set(key, [...(groups.get(key) || []), video]);
  }
  return [...groups.values()];
}

// The boom a capture was made for ("… · chat hype 04:24 (2.6x)"), else when it was captured (its id starts with the time).
function captureLabel(video) {
  const boom = /chat hype (\d{1,2}:\d{2})/.exec(video.title), made = /^\d{8}-(\d{2})(\d{2})/.exec(video.id);
  return boom ? `Boom at ${boom[1]}` : made ? `Captured ${made[1]}:${made[2]}` : "";
}

function thumb(node, video, clips) {
  const img = node.querySelector(".group-thumb img"), id = youtubeId(video.url);
  const best = [...clips].sort((a, b) => b.score - a.score)[0];
  const poster = best && `/api/videos/${best.video.id}/clips/${best.index}/poster.jpg`;
  img.addEventListener("error", () => {  // no thumbnail (offline, removed video): the best clip's poster, else the plain tile
    if (poster && !img.src.endsWith(poster)) img.src = poster; else img.hidden = true;
  });
  if (id) img.src = `https://i.ytimg.com/vi/${id}/mqdefault.jpg`;
  else if (poster) img.src = poster;
  else img.hidden = true;
  const time = node.querySelector(".thumb-time");
  time.textContent = video.live ? "Live" : video.duration ? clock(video.duration) : "";
  time.classList.toggle("is-live", Boolean(video.live));
  time.hidden = !time.textContent;
}

function clipCard(clip, showWhen) {
  const video = clip.video, node = el("clip-card").content.firstElementChild.cloneNode(true);
  const link = node.querySelector(".clip-card-link"), length = clock(Math.max(0, clip.end - clip.start));
  const score = Math.round(clip.score * 10) / 10, at = showWhen ? captureLabel(video) : "";
  link.href = `#/clip/${video.id}/${clip.index}`;
  link.setAttribute("aria-label", `${clip.title || "Untitled clip"}, score ${score} of 10, ${length}${at ? `, ${at.toLowerCase()}` : ""}`);
  const poster = node.querySelector(".clip-card-poster");
  poster.src = `/api/videos/${video.id}/clips/${clip.index}/poster.jpg`;
  poster.addEventListener("error", () => { poster.hidden = true; }, { once: true });
  node.querySelector(".clip-card-media").style.aspectRatio = (clip.shape || video.shape || "9:16").replace(":", " / ");
  node.querySelector(".card-score").textContent = `${score}/10`;
  node.querySelector(".card-length").textContent = length;
  const status = node.querySelector(".card-status");
  if (clip.status !== "new") { status.textContent = clip.status === "kept" ? "Kept" : "Dropped"; status.hidden = false; }
  node.classList.toggle("is-kept", clip.status === "kept");
  node.classList.toggle("is-dropped", clip.status === "discarded");
  node.querySelector(".clip-card-title").textContent = clip.title || "Untitled clip";
  const atEl = node.querySelector(".clip-card-when");
  atEl.textContent = at; atEl.hidden = !at;
  node.querySelector(".clip-card-hook").textContent = clip.hook;
  return node;
}

function moreCard(href, hidden) {
  const li = document.createElement("li");
  li.className = "clip-card more-card";
  const a = Object.assign(document.createElement("a"), { href, className: "more-card-link" });
  a.append(Object.assign(document.createElement("strong"), { textContent: `+${hidden}` }),
           Object.assign(document.createElement("span"), { textContent: `more clip${hidden === 1 ? "" : "s"}` }));
  li.append(a);
  return li;
}

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

// One video, or every capture of one stream. members[0] is the newest.
function row(members) {
  const video = members[0], stream = members.length > 1, node = el("video-row").content.firstElementChild.cloneNode(true);
  const clips = members.flatMap((m) => m.clips.map((c) => ({ ...c, video: m })));
  const fresh = clips.filter((c) => c.status === "new"), kept = clips.filter((c) => c.status === "kept").length;
  const target = (fresh[0] || clips[0])?.video || video;  // review starts where there is something left to decide
  const home = `#/video/${target.id}`;
  node.querySelector(".video-link").href = home;
  node.querySelector(".group-thumb").href = home;
  const review = node.querySelector(".group-review");
  review.href = home; review.hidden = !clips.length;
  review.textContent = fresh.length ? `Review ${fresh.length} new` : "Review clips";
  node.querySelector(".video-name").textContent = stream ? streamTitle(video.title) : video.title.replace(/[?&]si=[\w-]+/, "");
  node.querySelector(".video-sub").textContent = (stream ? [video.channel, plural(members.length, "capture")]
    : [video.channel, video.duration ? clock(video.duration) : "", video.shape]).filter(Boolean).join(" · ");
  thumb(node, video, clips);
  const best = [...clips].sort((a, b) => (a.status === "discarded") - (b.status === "discarded") || b.score - a.score);
  const shown = best.length > MAX_CARDS ? best.slice(0, MAX_CARDS - 1) : best;
  node.querySelector(".clip-cards").replaceChildren(...shown.map((c) => clipCard(c, stream)),
    ...(best.length > shown.length ? [moreCard(home, best.length - shown.length)] : []));

  const stateEl = node.querySelector(".video-state");
  const watching = members.find((m) => m.watching), busy = members.find((m) => m.job), failed = members.find((m) => m.stage === "failed" || (m.stage !== "ready" && !m.job));
  if (watching) {
    stateEl.textContent = watching.watching; stateEl.classList.add("is-busy");
  } else if (busy) {
    stateEl.textContent = busy.stage === "new" || busy.stage === "queued" ? "Waiting…" : `${busy.stage}…`; stateEl.classList.add("is-busy");
  } else if (failed && !clips.length) {
    stateEl.textContent = failed.stage === "failed" ? "Stopped: open it to see why" : "Stopped: open it to try again"; stateEl.classList.add("is-failed");
  } else {
    stateEl.textContent = [plural(clips.length, "clip"), fresh.length && clips.length > fresh.length ? `${fresh.length} new` : "", kept ? `${kept} kept` : ""]
      .filter(Boolean).join(" · ");
  }
  const watch = node.querySelector(".video-watch"), live = members.find((m) => m.live && m.stage === "ready");
  if (watching) {
    Object.assign(watch.dataset, { id: watching.id, action: "stop" }); watch.textContent = "Stop watching"; watch.hidden = false;
  } else if (live) {
    Object.assign(watch.dataset, { id: live.id, action: "start" }); watch.textContent = "Watch chat"; watch.hidden = false;
  }
  const del = node.querySelector(".video-delete"), title = stream ? streamTitle(video.title) : video.title;
  del.dataset.ids = members.map((m) => m.id).join(",");
  del.dataset.title = stream ? `${title}" (${plural(members.length, "capture")}` : title;
  del.setAttribute("aria-label", `Delete ${title}`);
  del.title = stream ? "Delete every capture of this stream and their clips" : "Delete video and its clips";
  del.hidden = members.some((m) => m.job || m.watching);
  return node;
}

async function addVideo(event) {
  event.preventDefault();
  showAlert("");
  const f = event.currentTarget.elements;
  const file = f.file.files[0];
  if (!f.url.value.trim() && !file) { showAlert("Paste a YouTube link or choose a video file first."); f.url.focus(); return; }
  el("add-button").disabled = true;
  try {
    const options = { shape: f.shape.value, max_clips: Number(f.max_clips.value), min_s: Number(f.min_s.value),
                      max_s: Number(f.max_s.value), direction: f.direction.value.trim(), live_minutes: Number(f.live_minutes.value) };
    let id;
    if (file) {  // an upload has no live chat
      el("add-button").lastElementChild.textContent = "Uploading…";
      const form = new FormData();
      form.append("file", file);
      form.append("language", f.language.value);
      Object.entries(options).forEach(([k, v]) => form.append(k, String(v)));
      const response = await fetch("/api/uploads", { method: "POST", body: form });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : "The upload failed.");
      id = body.id;
    } else {
      ({ id } = await api("/api/videos", "POST", { url: f.url.value.trim(), ...options, watch_chat: f.watch_chat.checked }));
    }
    f.url.value = ""; f.direction.value = ""; f.file.value = ""; showFile();
    location.hash = `#/video/${id}`;
  } catch (error) { showAlert(error.message); } finally { el("add-button").disabled = false; el("add-button").lastElementChild.textContent = "Find clips"; }
}

async function toggleWatch(event) {
  const button = event.target.closest(".video-watch");
  if (!button) return;
  button.disabled = true;
  try {
    await api(`/api/videos/${button.dataset.id}/watch`, button.dataset.action === "stop" ? "DELETE" : "POST");
    await refresh();
  } catch (error) { showAlert(error.message); button.disabled = false; }
}

async function deleteVideo(event) {
  const button = event.target.closest(".video-delete");
  if (!button) return;
  if (!confirm(`Delete "${button.dataset.title}" and all its clips? This cannot be undone.`)) return;
  try {
    for (const id of button.dataset.ids.split(",")) await api(`/api/videos/${id}`, "DELETE");
  } catch (error) { showAlert(error.message); }
  await refresh();
}
