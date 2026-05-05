// Sidebar — left rail with brand, nav, camera list, system links, and storage meter.
// Re-rendered on every route change so active states stay in sync.

import { api, escapeHtml, icon, statusKind, formatBytes } from "/static/v2/app.js";

let cachedCameras = null;
let cachedStats = null;
let lastFetch = 0;

function cameraThumbScene(camera) {
  // Cheap deterministic mapping so each camera gets a consistent placeholder
  // gradient until the real latest frame loads.
  const id = camera.camera_id || "";
  const sum = [...id].reduce((s, ch) => s + ch.charCodeAt(0), 0);
  const scenes = ["scene-day", "scene-overcast", "scene-dusk", "scene-night"];
  return scenes[sum % scenes.length];
}

function isCameraActive(hash, camId) {
  return hash.startsWith(`#/cameras/${encodeURIComponent(camId)}`);
}

async function loadIfStale() {
  // Cheap throttle — sidebar only refreshes every 30s when re-rendering.
  if (cachedCameras && Date.now() - lastFetch < 30000) return;
  try {
    const data = await api.fetchJson("/api/cameras");
    cachedCameras = data.cameras || [];
    cachedStats = data.stats || null;
    lastFetch = Date.now();
  } catch (e) {
    if (!cachedCameras) cachedCameras = [];
  }
}

function statusGlyph(status) {
  switch (statusKind(status)) {
    case "live":   return '<span style="color:var(--green)">● LIVE</span>';
    case "paused": return '<span>○ PAUSED</span>';
    case "failed": return '<span style="color:var(--red)">✕ ERROR</span>';
    default:       return '<span>○ OFFLINE</span>';
  }
}

function storageMeter() {
  if (!cachedStats || !cachedStats.storage_bytes) return "";
  const used = cachedStats.storage_bytes;
  const cap  = cachedStats.storage_capacity_bytes || (used * 2);
  const pct  = Math.min(100, Math.round((used / cap) * 100));
  return `
    <div class="storage-meter">
      <div class="row" style="gap:6px">
        ${icon("server", 12)}
        <span class="lbl ink" style="font-size:9px">STORAGE</span>
      </div>
      <div class="num" style="font-size:11px;margin-top:4px">${formatBytes(used)} / ${formatBytes(cap)}</div>
      <div class="storage-bar"><span style="width:${pct}%"></span></div>
    </div>`;
}

export async function renderSidebar(hash) {
  const root = document.getElementById("sidebar");
  if (!root) return;
  await loadIfStale();
  const cameras = cachedCameras || [];

  const dashActive = hash.startsWith("#/dashboard")     ? "active" : "";
  const libActive  = hash.startsWith("#/library")       ? "active" : "";
  const setActive  = hash.startsWith("#/settings")      ? "active" : "";

  root.innerHTML = `
    <div class="brand">
      <div class="brand-mark">TL</div>
      <div class="brand-name">TIMELAPSE</div>
    </div>

    <div class="side-list">
      <a class="side-item ${dashActive}" href="#/dashboard">${icon("home", 14)}<span>Dashboard</span></a>
      <a class="side-item ${libActive}"  href="#/library">${icon("film", 14)}<span>Library</span></a>
    </div>

    <div class="side-section">
      <div class="between" style="padding:0 6px">
        <div class="lbl">Cameras · ${cameras.length}</div>
        <a class="btn ghost sm" href="#/agents/new" title="Add camera">${icon("plus", 12)}</a>
      </div>
      <div class="side-list" style="margin-top:6px">
        ${cameras.length === 0
          ? `<div class="small" style="padding:6px 8px">No cameras yet.</div>`
          : cameras.map(c => {
              const active = isCameraActive(hash, c.camera_id) ? "active" : "";
              const display = c.config?.display_name || c.camera_id;
              return `
                <a class="side-cam ${active}" href="#/cameras/${encodeURIComponent(c.camera_id)}">
                  <div class="thumb scene ${cameraThumbScene(c)}"></div>
                  <div class="grow">
                    <div class="name">${escapeHtml(display)}</div>
                    <div class="meta">${statusGlyph(c.status)}</div>
                  </div>
                </a>`;
            }).join("")
        }
      </div>
    </div>

    <div class="side-section">
      <div class="lbl" style="padding:0 6px">System</div>
      <div class="side-list">
        <a class="side-item ${setActive}" href="#/settings">${icon("settings", 14)}<span>Settings</span></a>
      </div>
    </div>

    ${storageMeter()}
  `;
}

// Allow other views (e.g. after creating/deleting a camera) to nuke the cache.
export function invalidateSidebar() {
  cachedCameras = null;
  cachedStats = null;
  lastFetch = 0;
}
