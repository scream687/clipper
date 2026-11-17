// Router and settings. Screens: #/ library, #/video/<id>, #/clip/<id>/<n>.

import { api, el, showAlert } from "./util.js";
import { leaveLibrary, showLibrary, wireLibrary } from "./library.js";
import { leaveVideo, showVideo, wireVideo } from "./video.js";
import { leaveEditor, scrollToClipStart, showEditor } from "./editor.js";

const VIEWS = ["view-library", "view-video", "view-editor"];

function show(view) {
  VIEWS.forEach((id) => { el(id).hidden = id !== view; });
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
  el("settings-form").elements.handle.value = settings.handle;
  el("settings-form").elements.channels.value = settings.channels.join("\n");
}

el("settings-toggle").addEventListener("click", () => {
  const open = el("settings").hidden;
  el("settings").hidden = !open;
  el("settings-toggle").setAttribute("aria-expanded", String(open));
});
el("settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const f = event.currentTarget.elements;
    await api("/api/settings", "PUT", { handle: f.handle.value.trim(), channels: f.channels.value.split("\n").map((c) => c.trim()).filter(Boolean) });
    el("settings-status").textContent = "Saved. New previews and exports use it.";
  } catch (error) { el("settings-status").textContent = error.message; }
});

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
