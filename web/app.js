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
}

el("settings-toggle").addEventListener("click", () => {
  const open = el("settings").hidden;
  el("settings").hidden = !open;
  el("settings-toggle").setAttribute("aria-expanded", String(open));
});
el("settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api("/api/settings", "PUT", { handle: event.currentTarget.elements.handle.value.trim() });
    el("settings-status").textContent = "Saved. New previews and exports use it.";
  } catch (error) { el("settings-status").textContent = error.message; }
});

wireLibrary();
wireVideo();
loadSettings().catch((error) => showAlert(error.message));
window.addEventListener("hashchange", route);
route();
