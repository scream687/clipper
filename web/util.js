// Shared helpers. Server text (titles, transcript words) only ever reaches the page via textContent.

export const el = (id) => document.getElementById(id);

export function clock(seconds) {
  const total = Math.max(0, Math.floor(seconds));
  const h = Math.floor(total / 3600), m = Math.floor((total % 3600) / 60), s = String(total % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`;
}

function describeError(body, status) {
  const detail = body && body.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((d) => String(d.msg || "").replace(/^Value error, /, "")).join("\n");
  return `The request failed (${status}).`;
}

export async function api(path, method = "GET", payload) {
  let response;
  try {
    const form = payload instanceof FormData;  // file uploads go as multipart; the browser sets the boundary
    response = await fetch(path, {
      method,
      headers: payload && !form ? { "content-type": "application/json" } : {},
      body: payload ? (form ? payload : JSON.stringify(payload)) : undefined,
    });
  } catch {
    throw new Error("Cannot reach the clipper server. Is run.sh still running?");
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(describeError(body, response.status));
  return body;
}

export function showAlert(message) {
  el("alert").textContent = message;
  el("alert").hidden = !message;
}

export async function copyText(button, text) {
  const original = button.textContent;
  try {
    await navigator.clipboard.writeText(text);
    button.textContent = "Copied";
  } catch {
    button.textContent = "Copy failed";
  }
  setTimeout(() => { button.textContent = original; }, 1500);
}

export function wait(ms) { return new Promise((r) => setTimeout(r, ms)); }
