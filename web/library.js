// Library: add a video, list every video with its progress.

import { api, clock, el, showAlert } from "./util.js";

const POLL_MS = 2500;
const DONE = new Set(["ready", "failed"]);
let timer = null;

export function wireLibrary() {
  el("add-form").addEventListener("submit", addVideo);
  el("video-list").addEventListener("click", deleteVideo);
}

export async function showLibrary() {
  document.title = "Clipper";
  await refresh();
}

export function leaveLibrary() { clearTimeout(timer); }

async function refresh() {
  clearTimeout(timer);
  let videos;
  try { videos = await api("/api/videos"); } catch (error) { showAlert(error.message); return; }
  el("library-empty").hidden = videos.length > 0;
  el("video-list").replaceChildren(...videos.map(row));
  if (videos.some((v) => v.job || !DONE.has(v.stage))) timer = setTimeout(refresh, POLL_MS);
}

function row(video) {
  const node = el("video-row").content.firstElementChild.cloneNode(true);
  node.querySelector(".video-link").href = `#/video/${video.id}`;
  node.querySelector(".video-name").textContent = video.title;
  node.querySelector(".video-sub").textContent = [video.channel, video.duration ? clock(video.duration) : "", video.shape].filter(Boolean).join(" · ");
  const stateEl = node.querySelector(".video-state");
  if (video.stage === "failed") {
    stateEl.textContent = "Stopped: open it to see why"; stateEl.classList.add("is-failed");
  } else if (video.stage === "ready") {
    stateEl.textContent = `${video.clip_count} clip${video.clip_count === 1 ? "" : "s"}${video.kept ? ` · ${video.kept} exported` : ""}`;
  } else {
    stateEl.textContent = video.stage === "new" || video.stage === "queued" ? "Waiting…" : `${video.stage}…`;
    stateEl.classList.add("is-busy");
  }
  const del = node.querySelector(".video-delete");
  del.dataset.id = video.id;
  del.dataset.title = video.title;
  del.hidden = Boolean(video.job);
  return node;
}

async function addVideo(event) {
  event.preventDefault();
  showAlert("");
  const f = event.currentTarget.elements;
  if (!f.url.value.trim()) { showAlert("Paste a YouTube link first."); f.url.focus(); return; }
  el("add-button").disabled = true;
  try {
    const { id } = await api("/api/videos", "POST", {
      url: f.url.value.trim(), shape: f.shape.value, max_clips: Number(f.max_clips.value),
      min_s: Number(f.min_s.value), max_s: Number(f.max_s.value), direction: f.direction.value.trim(),
    });
    f.url.value = ""; f.direction.value = "";
    location.hash = `#/video/${id}`;
  } catch (error) { showAlert(error.message); } finally { el("add-button").disabled = false; }
}

async function deleteVideo(event) {
  const button = event.target.closest(".video-delete");
  if (!button) return;
  if (!confirm(`Delete "${button.dataset.title}" and all its clips? This cannot be undone.`)) return;
  try { await api(`/api/videos/${button.dataset.id}`, "DELETE"); await refresh(); } catch (error) { showAlert(error.message); }
}
