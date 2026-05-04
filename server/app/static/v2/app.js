// Timelapse v2 — vanilla JS shell
// Mirrors the architecture of the original /static/app.js (registerView pattern,
// hash-based router, fetchJson helper) so the redesigned views drop into the
// existing FastAPI server with no behaviour change.

import { renderSidebar } from "/static/v2/components/sidebar.js";

const views = new Map();
let cleanupActiveView = null;

export function registerView(prefix, render) {
  views.set(prefix, render);
}

// ---- Shared utilities --------------------------------------------------------

export function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

export async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`${response.status}: ${detail}`);
  }
  return response.status === 204 ? null : response.json();
}

export const api = { fetchJson };

export function relativeTime(iso) {
  if (!iso) return "—";
  const seconds = (Date.now() - new Date(iso).getTime()) / 1000;
  if (Number.isNaN(seconds)) return "—";
  if (seconds < 60) return `${Math.max(0, Math.round(seconds))}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h ago`;
  return `${Math.round(seconds / 86400)}d ago`;
}

export function formatBytes(bytes) {
  if (!bytes) return "0 B";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

export function fmtNum(n) {
  if (n == null) return "—";
  return Number(n).toLocaleString("en-US").replace(/,/g, " ");
}

// Status helpers — reused from the original app
export function statusKind(status) {
  if (!status) return "unknown";
  if (status.is_online && status.in_schedule === false) return "paused";
  if (status.is_online) return "live";
  if (status.last_error) return "failed";
  return "offline";
}

export function statusLabel(status) {
  switch (statusKind(status)) {
    case "live":   return "● LIVE";
    case "paused": return "○ PAUSED";
    case "failed": return "✕ ERROR";
    case "offline":return "○ OFFLINE";
    default:       return "○ NEVER SEEN";
  }
}

// ---- Lucide-style icon helper ------------------------------------------------
// Returns inline SVG markup so views can interpolate icons into HTML strings.
const ICON_PATHS = {
  home:     '<path d="M3 12 12 4l9 8"/><path d="M5 10v10h14V10"/>',
  camera:   '<rect x="3" y="6" width="18" height="14" rx="2"/><circle cx="12" cy="13" r="4"/><path d="M8 6l2-3h4l2 3"/>',
  plus:     '<path d="M12 5v14M5 12h14"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3h.1a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8v.1a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
  film:     '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 7h4M3 12h4M3 17h4M17 7h4M17 12h4M17 17h4M7 3v18M17 3v18"/>',
  play:     '<polygon points="6 4 20 12 6 20 6 4"/>',
  pause:    '<rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/>',
  download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>',
  edit:     '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 1 1 3 3L7 19l-4 1 1-4 12.5-12.5z"/>',
  trash:    '<polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/>',
  chev:     '<polyline points="9 18 15 12 9 6"/>',
  arrow:    '<line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/>',
  back:     '<line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/>',
  x:        '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
  sun:      '<circle cx="12" cy="12" r="4"/><line x1="12" y1="2" x2="12" y2="4"/><line x1="12" y1="20" x2="12" y2="22"/><line x1="4.93" y1="4.93" x2="6.34" y2="6.34"/><line x1="17.66" y1="17.66" x2="19.07" y2="19.07"/><line x1="2" y1="12" x2="4" y2="12"/><line x1="20" y1="12" x2="22" y2="12"/><line x1="4.93" y1="19.07" x2="6.34" y2="17.66"/><line x1="17.66" y1="6.34" x2="19.07" y2="4.93"/>',
  moon:     '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>',
  bell:     '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.7 21a2 2 0 0 1-3.4 0"/>',
  copy:     '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
  check:    '<polyline points="20 6 9 17 4 12"/>',
  refresh:  '<polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>',
  clock:    '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
  wifi:     '<path d="M5 12.55a11 11 0 0 1 14 0"/><path d="M2 8.5a16 16 0 0 1 20 0"/><path d="M8.5 16.4a6 6 0 0 1 7 0"/><line x1="12" y1="20" x2="12.01" y2="20"/>',
  server:   '<rect x="2" y="2" width="20" height="8" rx="2"/><rect x="2" y="14" width="20" height="8" rx="2"/><line x1="6" y1="6" x2="6.01" y2="6"/><line x1="6" y1="18" x2="6.01" y2="18"/>',
  activity: '<polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>',
  alert:    '<circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>',
  ext:      '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/>',
};

export function icon(name, size = 14, extraClass = "") {
  const path = ICON_PATHS[name] || "";
  const cls = `ico${extraClass ? " " + extraClass : ""}`;
  return `<svg class="${cls}" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">${path}</svg>`;
}

// ---- Topbar ------------------------------------------------------------------

/**
 * Render the topbar.
 * @param {Array<{label:string, href?:string}>|string[]} crumbs
 * @param {string} rightHtml - already-escaped HTML for the right side
 */
export function renderTopbar(crumbs, rightHtml = "") {
  const root = document.getElementById("topbar");
  if (!root) return;
  const items = (crumbs || []).map((c, i) => {
    const isLast = i === crumbs.length - 1;
    const isObj = typeof c === "object" && c !== null;
    const label = escapeHtml(isObj ? c.label : c);
    const href  = isObj ? c.href : null;
    const sep   = i > 0 ? '<span class="sep">/</span>' : "";
    if (href && !isLast) return `${sep}<a href="${escapeHtml(href)}">${label}</a>`;
    return `${sep}<span class="${isLast ? "here" : ""}">${label}</span>`;
  }).join("");
  root.outerHTML = `
    <div id="topbar" class="topbar">
      <div class="crumbs">${items}</div>
      <div class="row">${rightHtml}</div>
    </div>`;
}

// ---- Modal helpers -----------------------------------------------------------

export function openModal(html, opts = {}) {
  const root = document.getElementById("modal-root");
  root.innerHTML = `
    <div class="modal-backdrop" data-close="1">
      <div class="modal ${opts.wide ? "wide" : ""}">${html}</div>
    </div>`;
  const backdrop = root.firstElementChild;
  function close() { root.innerHTML = ""; if (opts.onClose) opts.onClose(); }
  backdrop.addEventListener("click", (e) => {
    if (e.target.dataset.close || e.target.closest("[data-modal-close]")) close();
  });
  document.addEventListener("keydown", function esc(e) {
    if (e.key === "Escape") { close(); document.removeEventListener("keydown", esc); }
  });
  return { close, root };
}

// ---- Router ------------------------------------------------------------------

function showError(root, error) {
  root.innerHTML = `
    <div class="empty">
      <h3>Something went wrong</h3>
      <div class="mono small">${escapeHtml(error.message)}</div>
    </div>`;
}

async function render() {
  const root = document.getElementById("app-root");
  const hash = window.location.hash || "#/dashboard";

  if (cleanupActiveView) {
    try { cleanupActiveView(); } catch (e) { /* noop */ }
    cleanupActiveView = null;
  }

  // Re-render the sidebar on every navigation so the active state updates.
  renderSidebar(hash);

  for (const [prefix, view] of views.entries()) {
    if (hash.startsWith(prefix)) {
      root.replaceChildren();
      try {
        const cleanup = await view(root, hash);
        if (typeof cleanup === "function") cleanupActiveView = cleanup;
      } catch (error) {
        console.error(error);
        showError(root, error);
      }
      return;
    }
  }
  root.innerHTML = `<div class="empty"><h3>Not found</h3><div class="mono small">${escapeHtml(hash)}</div></div>`;
}

window.addEventListener("hashchange", render);

if (!window.location.hash) {
  window.location.hash = "#/dashboard";
}

// Lazy-load all views, then do the first render.
Promise.all([
  import("/static/v2/views/dashboard.js"),
  import("/static/v2/views/camera.js"),
  import("/static/v2/views/create-agent.js"),
  import("/static/v2/views/library.js"),
  import("/static/v2/views/settings.js"),
])
  .then(render)
  .catch((error) => {
    console.error(error);
    const root = document.getElementById("app-root");
    if (root) showError(root, error);
  });
