// Router and settings. Screens: #/ library, #/video/<id>, #/clip/<id>/<n>.

import { api, clock, el, showAlert } from "./util.js";
import { leaveLibrary, showLibrary, wireLibrary } from "./library.js";
import { leaveVideo, showVideo, wireVideo } from "./video.js";
import { leaveEditor, scrollToClipStart, showEditor } from "./editor.js";

const VIEWS = ["view-library", "view-video", "view-editor"];

function show(view) {
  VIEWS.forEach((id) => { el(id).hidden = id !== view; });
  const videos = document.querySelector('.side-link[data-scroll="view-library"]');
  videos.setAttribute("aria-current", view === "view-library" ? "page" : "true");  // review and editor live under Videos
}

// Sidebar: jump to a part of the library (from another screen, once the library has drawn).
document.querySelectorAll(".side-link[data-scroll]").forEach((link) => link.addEventListener("click", (event) => {
  const go = () => el(link.dataset.scroll).scrollIntoView({ block: "start", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  if (!el("view-library").hidden) { event.preventDefault(); go(); return; }
  window.addEventListener("hashchange", () => setTimeout(go, 300), { once: true });  // ponytail: a fixed wait for the library to draw
}));

function showStreamers(handles) {
  el("side-streamers").replaceChildren(...handles.map((handle) => {
    const li = document.createElement("li");
    const a = Object.assign(document.createElement("a"), { href: `https://www.youtube.com/${handle}/live`, target: "_blank", rel: "noopener noreferrer",
      className: "side-link side-streamer", title: `Open ${handle}'s live page on YouTube` });
    a.innerHTML = '<svg class="icon"><use href="#i-radio"/></svg>';
    a.append(Object.assign(document.createElement("span"), { textContent: handle }));
    li.append(a);
    return li;
  }));
  el("side-streamers-empty").hidden = handles.length > 0;
}

async function route() {
  showAlert("");
  leaveLibrary(); leaveVideo(); leaveEditor();
  const parts = location.hash.replace(/^#\/?/, "").split("/").map(decodeURIComponent);
  try {
    if (parts[0] === "video" && parts[1]) {
      show("view-video"); await showVideo(parts[1]);
    } else if (parts[0] === "clip" && parts[1] && /^\d+$/.test(parts[2] || "")) {
      show("view-editor"); await showEditor(parts[1], Number(parts[2])); scrollToClipStart();
    } else {
      show("view-library"); await showLibrary();
    }
  } catch (error) {
    showAlert(error.message);
  }
}

async function loadSettings() {
  const settings = await api("/api/settings");
  const form = el("settings-form").elements;
  form.handle.value = settings.handle || "";
  form.brand.value = settings.brand || "";
  for (const name of ["channels", "live_channels", "upcoming", "trend_creators"]) form[name].value = (settings[name] || []).join("\n");
  showStreamers(settings.live_channels || []);
  form.export_quality.value = settings.export_quality || "1080";
  form.export_fps.value = settings.export_fps || "source";
  form.auto_upload.checked = Boolean(settings.auto_upload);
  showOpusKey(settings.has_opusclip_key);
  showPexelsKey(settings.has_pexels_key);
  loadYouTube();
}

// ---------- YouTube channel: connect, daily uploads ----------
let youtubeTimer = null;

async function loadYouTube() {
  clearTimeout(youtubeTimer);
  let y;
  try { y = await api("/api/youtube"); } catch (error) { el("youtube-state").textContent = error.message; return; }
  const connect = el("youtube-connect");
  if (!y.configured) {
    el("youtube-state").textContent = "Needs a Google OAuth file first: put client_secret.json in the Clipper folder (README: YouTube uploads).";
    connect.hidden = true;
  } else if (y.connected && !y.error) {
    el("youtube-state").textContent = `Connected to ${y.channel || "your channel"}. ${y.auto_upload ? `Posting daily at ${y.slots.join(" and ")} IST.` : "Daily posting is off."}`;
    connect.hidden = false; connect.textContent = "Disconnect"; connect.dataset.action = "disconnect";
  } else {
    el("youtube-state").textContent = y.error ? `Sign-in expired: connect again. (${y.error})` : "Not connected.";
    connect.hidden = false; connect.textContent = "Connect"; connect.dataset.action = "connect";
  }
  el("youtube-run").hidden = !(y.connected && !y.error);
  el("youtube-uploads").replaceChildren(...y.uploads.map((u) => {
    const li = document.createElement("li");
    const a = document.createElement("a");
    a.href = `https://youtu.be/${u.youtube_id}`; a.target = "_blank"; a.rel = "noopener"; a.textContent = u.title;
    li.append(`${new Date(u.slot).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })} · `, a);
    return li;
  }));
  showPosting(y);
  if (y.job) youtubeTimer = setTimeout(loadYouTube, 4000);
}

// Auto-upload publishes to a public channel, so its real state is always on screen, not only inside Settings.
function showPosting(y) {
  const on = el("settings-form").elements.auto_upload.checked;
  const blocked = !y.configured ? "YouTube is not set up (client_secret.json missing)" :
    !y.connected ? "YouTube is not connected" : y.error ? "the YouTube sign-in expired" : "";
  const status = el("side-posting");
  status.hidden = false;
  status.className = `side-status ${on && blocked ? "is-blocked" : on ? "is-on" : ""}`;
  status.textContent = !on ? "Auto-posting is off" : blocked ? `Not posting: ${blocked}` :
    `Posting daily at ${y.slots.join(" and ")} IST${y.channel ? ` to ${y.channel}` : ""}`;
  const warning = el("auto-upload-warning");
  warning.hidden = !(on && blocked);
  warning.textContent = `Nothing will post until this is fixed: ${blocked}. See YouTube channel below.`;
}

el("youtube-connect").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    if (button.dataset.action === "disconnect") {
      await api("/api/youtube", "DELETE");
    } else {
      el("youtube-state").textContent = "Finish the sign-in in the browser tab that opened…";
      const { job } = await api("/api/youtube/connect", "POST");
      for (let i = 0; i < 150; i++) {  // up to 5 minutes for the Google sign-in
        await new Promise((r) => setTimeout(r, 2000));
        const j = await api(`/api/jobs/${job}`);
        if (j.status === "error") throw new Error(j.error);
        if (j.status === "done") break;
      }
    }
  } catch (error) { showAlert(error.message); }
  button.disabled = false;
  loadYouTube();
});

el("youtube-run").addEventListener("click", async () => {
  try { await api("/api/youtube/run", "POST"); el("youtube-state").textContent = "Rendering and uploading the next clips…"; }
  catch (error) { showAlert(error.message); }
  setTimeout(loadYouTube, 1500);
});

function showPexelsKey(saved) {
  const field = el("settings-form").elements.pexels_key;
  field.value = ""; field.placeholder = saved ? "Saved (leave blank to keep)" : "";
}

function showOpusKey(saved) {
  const field = el("settings-form").elements.opusclip_key;
  field.value = "";
  field.placeholder = saved ? "Saved. Leave blank to keep it." : "From OpusClip → Settings → API";
}

const lines = (text) => text.split("\n").map((c) => c.trim()).filter(Boolean);

el("settings-form").elements.auto_upload.addEventListener("change", () => loadYouTube());
el("settings-toggle").addEventListener("click", () => {
  const open = el("settings").hidden;
  el("settings").hidden = !open;
  el("settings-toggle").setAttribute("aria-expanded", String(open));
});
el("settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const f = event.currentTarget.elements;
    const saved = await api("/api/settings", "PUT", { handle: f.handle.value.trim(), brand: f.brand.value.trim(), opusclip_key: f.opusclip_key.value.trim(), pexels_key: f.pexels_key.value.trim(),
      channels: lines(f.channels.value), live_channels: lines(f.live_channels.value), upcoming: lines(f.upcoming.value), trend_creators: lines(f.trend_creators.value),
      export_quality: f.export_quality.value, export_fps: f.export_fps.value, auto_upload: f.auto_upload.checked });
    showOpusKey(saved.has_opusclip_key);
    showPexelsKey(saved.has_pexels_key);
    el("settings-status").textContent = "Saved. New previews and exports use it.";
    showStreamers(lines(f.live_channels.value));
    loadYouTube();
  } catch (error) { el("settings-status").textContent = error.message; }
});

// ---------- brand: logo and music library ----------
const fileForm = (file) => { const f = new FormData(); f.append("file", file); return f; };

function showLogo(info) {
  el("logo-preview").hidden = !info.has_logo;
  if (info.has_logo) el("logo-preview").src = `/api/logo.png?v=${Date.now()}`;
  el("logo-remove").hidden = !info.has_logo;
}

function showMusic(tracks) {
  el("music-list").replaceChildren(...tracks.map((t) => {
    const row = document.createElement("li"), remove = document.createElement("button");
    row.textContent = `${t.name} · ${clock(t.duration)}`;
    Object.assign(remove, { type: "button", className: "text-button", textContent: "Delete" });
    remove.dataset.name = t.name;
    row.append(remove);
    return row;
  }));
}

async function brandAction(work, done) {
  el("brand-status").textContent = "Working…";
  try { done(await work()); el("brand-status").textContent = "Saved."; } catch (error) { el("brand-status").textContent = error.message; }
}

el("logo-file").addEventListener("change", (e) => {
  const file = e.target.files[0];
  if (file) brandAction(() => api("/api/logo", "POST", fileForm(file)), showLogo).finally(() => { e.target.value = ""; });
});
el("logo-remove").addEventListener("click", () => brandAction(() => api("/api/logo", "DELETE"), showLogo));
el("music-file").addEventListener("change", (e) => {
  const file = e.target.files[0];
  if (file) brandAction(() => api("/api/music", "POST", fileForm(file)), showMusic).finally(() => { e.target.value = ""; });
});
el("music-list").addEventListener("click", (e) => {
  const name = e.target.dataset?.name;
  if (name && confirm(`Delete ${name} from the music library?`)) brandAction(() => api(`/api/music/${encodeURIComponent(name)}`, "DELETE"), showMusic);
});
api("/api/logo").then(showLogo).catch(() => {});
api("/api/music").then(showMusic).catch(() => {});

function showSounds(sounds) {
  const roles = { meme: "punchline meme", whoosh: "whoosh", pop: "pop", impact: "impact", riser: "riser" };
  el("sfx-list").replaceChildren(...sounds.map((s) => {
    const row = document.createElement("li"), play = document.createElement("button");
    Object.assign(play, { type: "button", className: "text-button", textContent: "Play" });
    play.dataset.play = s.name;
    row.append(`${s.name} · ${roles[s.role] || s.role}${s.built_in ? " · built in" : s.safe ? "" : " · may be claimed"} `, play);
    if (!s.built_in) {
      const remove = document.createElement("button");
      Object.assign(remove, { type: "button", className: "text-button", textContent: "Delete" });
      remove.dataset.name = s.name;
      row.append(" ", remove);
    }
    return row;
  }));
}
el("sfx-import").addEventListener("click", () => {
  const url = el("sfx-url").value.trim();
  if (!url) { el("brand-status").textContent = "Paste a myinstants.com link first."; return; }
  brandAction(() => api("/api/sfx/import", "POST", { url, role: el("sfx-role").value }), showSounds).then(() => { el("sfx-url").value = ""; });
});
el("sfx-file").addEventListener("change", (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const form = fileForm(file); form.append("role", el("sfx-role").value);
  brandAction(() => api("/api/sfx", "POST", form), showSounds).finally(() => { e.target.value = ""; });
});
el("sfx-list").addEventListener("click", (e) => {
  const { play, name } = e.target.dataset || {};
  if (play) new Audio(`/api/sfx/${encodeURIComponent(play)}`).play().catch(() => {});
  if (name && confirm(`Delete ${name}?`)) brandAction(() => api(`/api/sfx/${encodeURIComponent(name)}`, "DELETE"), showSounds);
});
api("/api/sfx").then(showSounds).catch(() => {});

// ---------- AI provider and model ----------
const ai = { providers: [], keyUrls: [], models: [] };
const aiFields = () => el("ai-form").elements;
const HIDDEN_FACTS = new Set(["id", "object", "type", "canonical_slug", "hugging_face_id"]);

function flat(value) {
  if (Array.isArray(value)) return value.filter((v) => typeof v !== "object").join(", ");
  if (value && typeof value === "object") {
    return Object.entries(value).filter(([, v]) => v !== null && v !== "").map(([k, v]) => `${k}: ${flat(v)}`).join(" · ");
  }
  return typeof value === "number" ? value.toLocaleString() : String(value);
}

function showFacts() {
  const model = ai.models.find((m) => m.id === aiFields().model.value);
  const facts = el("ai-facts");
  facts.replaceChildren();
  Object.entries(model || {}).filter(([k, v]) => !HIDDEN_FACTS.has(k) && v !== null && v !== "").forEach(([key, value]) => {
    const dt = document.createElement("dt"), dd = document.createElement("dd");
    dt.textContent = key.replace(/_/g, " ");
    dd.textContent = key === "created" && typeof value === "number" ? new Date(value * 1000).toLocaleDateString() : flat(value);
    dd.title = dd.textContent;
    facts.append(dt, dd);
  });
  facts.hidden = !facts.childElementCount;
}

function setModels(models) {
  ai.models = models;
  el("ai-models").replaceChildren(...models.map((m) => {
    const option = document.createElement("option");
    option.value = m.id;
    if (m.name || m.display_name) option.label = m.name || m.display_name;
    return option;
  }));
  el("ai-model-count").textContent = models.length ? `${models.length} available` : "";
  showFacts();
}

function aiPayload() {
  const f = aiFields();
  return { provider: f.provider.value, base_url: f.base_url.value.trim(), api_key: f.api_key.value.trim(), model: f.model.value.trim() };
}

async function loadModels() {
  el("ai-status").textContent = "Asking the provider for its models…";
  try {
    setModels(await api("/api/ai/models", "POST", aiPayload()));
    el("ai-status").textContent = "";
  } catch (error) { setModels([]); el("ai-status").textContent = error.message; }
}

function showProvider(baseUrl) {
  const f = aiFields(), p = ai.providers.find((x) => x.id === f.provider.value);
  f.base_url.value = baseUrl ?? p.base_url;
  f.base_url.readOnly = !["ollama", "custom"].includes(p.id);
  el("ai-url-field").hidden = p.id === "claude-code";
  el("ai-key-field").hidden = p.id === "claude-code" || p.id === "ollama";
  const saved = ai.keyUrls.includes(f.base_url.value);
  f.api_key.value = "";
  f.api_key.placeholder = saved ? "Saved. Leave blank to keep it." : p.needs_key ? "Paste your key" : "Only if the API asks for one";
  setModels([]);
  if (p.id !== "custom" && (!p.needs_key || saved)) loadModels();
}

function applyAI(config) {
  ai.providers = config.providers; ai.keyUrls = config.key_urls;
  const f = aiFields();
  f.provider.replaceChildren(...config.providers.map((p) => new Option(p.label, p.id)));
  f.provider.value = config.provider;
  showProvider(config.base_url || undefined);
  f.model.value = config.model;
}

el("ai-form").elements.provider.addEventListener("change", () => { aiFields().model.value = ""; showProvider(); });
el("ai-form").elements.model.addEventListener("input", showFacts);
el("ai-load").addEventListener("click", loadModels);
el("ai-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const config = await api("/api/ai", "PUT", aiPayload());
    ai.keyUrls = config.key_urls;
    aiFields().api_key.value = "";
    aiFields().api_key.placeholder = "Saved. Leave blank to keep it.";
    el("ai-status").textContent = `Saved. New analyses use ${config.model}.`;
  } catch (error) { el("ai-status").textContent = error.message; }
});

wireLibrary();
wireVideo();
loadSettings().catch((error) => showAlert(error.message));
api("/api/ai").then(applyAI).catch((error) => showAlert(error.message));
window.addEventListener("hashchange", route);
route();
