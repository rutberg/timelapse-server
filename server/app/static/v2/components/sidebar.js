// Sidebar — left rail with brand, nav, camera list, system links, and storage meter.
// Re-rendered on every route change so active states stay in sync.

import { api, escapeHtml, icon, statusKind, formatBytes, rendersStore } from "/static/v2/app.js";

let cachedCameras = null;
let cachedStats = null;
let lastFetch = 0;
let latestSnap = { running: null, queued: [], recent: [] };
const armedCancels = new Set();
const cancellingIds = new Set();

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

function fmtRange(job) {
    if (job.range_preset === "all") return "all time";
    if (job.range_preset === "24h") return "24h";
    if (job.range_preset === "7d")  return "7d";
    if (job.start_at && job.end_at) return `${escapeHtml(job.start_at.slice(0,10))} → ${escapeHtml(job.end_at.slice(0,10))}`;
    return "—";
}

function fmtEta(seconds) {
    if (seconds == null) return "";
    const m = Math.floor(seconds / 60), s = seconds % 60;
    return `~${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")} left`;
}

function renderRow(job, kind) {
    const base = `${escapeHtml(job.camera_id)} · ${job.format.toUpperCase()} · ${fmtRange(job)}`;
    if (kind === "running") {
        const pct = job.percent ?? 0;
        const eta = fmtEta(job.eta_seconds);
        const frames = job.total_frames
            ? `${(job.current_frame || 0).toLocaleString()} / ${job.total_frames.toLocaleString()} frames`
            : "";
        const cancelling = job.cancel_requested ? "(cancelling…)" : "";
        return `
          <div class="render-row running">
            <div class="render-row-head">▶ Rendering · ${escapeHtml(job.camera_id)} · ${job.format.toUpperCase()} · ${fmtRange(job)}</div>
            <div class="render-bar"><span style="width:${pct}%"></span></div>
            <div class="render-meta">
              <span>${pct}% ${eta}</span>
              <button class="cancel-btn" data-cancel="${job.id}" data-kind="running" aria-label="Cancel">✕</button>
            </div>
            <div class="render-meta small">${frames} ${cancelling}</div>
          </div>`;
    }
    if (kind === "queued") {
        return `
          <div class="render-row queued">
            <div class="render-meta">
              <span>${base}</span>
              <button class="cancel-btn" data-cancel="${job.id}" data-kind="queued" aria-label="Remove from queue">✕</button>
            </div>
          </div>`;
    }
    const ico = job.status === "done" ? "✓" : job.status === "failed" ? "✕" : "⊘";
    const tip = job.error ? ` title="${escapeHtml(job.error)}"` : "";
    return `<div class="render-row recent" data-recent="${job.id}"${tip}>${ico} ${base}</div>`;
}

function serverPanel(stats, snap) {
    const used = stats?.storage_bytes ?? 0;
    const cap  = stats?.storage_capacity_bytes ?? (used * 2 || 1);
    const pct  = Math.min(100, Math.round((used / cap) * 100));
    const queue = snap.queued || [];
    const recent = (snap.recent || [])[0];
    return `
      <section class="server-panel">
        <div class="lbl">SERVER</div>

        <div class="server-row storage">
          <div class="row" style="gap:6px">${icon("server", 12)} <span class="lbl ink small">Storage</span></div>
          <div class="num small">${formatBytes(used)} / ${formatBytes(cap)}</div>
          <div class="storage-bar"><span style="width:${pct}%"></span></div>
        </div>

        ${snap.running ? renderRow(snap.running, "running") : ""}

        ${queue.length ? `
          <div class="queue-head between">
            <span class="lbl small">⌛ Queue · ${queue.length}</span>
          </div>
          ${queue.map(j => renderRow(j, "queued")).join("")}
        ` : ""}

        ${recent ? renderRow(recent, "recent") : ""}
      </section>
    `;
}

function wireServerPanel(panel) {
    if (!panel) return;
    panel.querySelectorAll(".cancel-btn").forEach((btn) => {
        const id = btn.dataset.cancel;
        if (cancellingIds.has(id)) {
            btn.disabled = true;
            btn.textContent = "…";
            return;
        }
        if (armedCancels.has(id)) {
            btn.classList.add("armed");
            btn.textContent = "Cancel?";
        }
    });
    panel.querySelectorAll(".cancel-btn").forEach((btn) => {
        btn.addEventListener("click", (e) => {
            e.preventDefault(); e.stopPropagation();
            const id = btn.dataset.cancel;
            if (cancellingIds.has(id)) return;
            if (armedCancels.has(id)) {
                armedCancels.delete(id);
                cancellingIds.add(id);
                btn.disabled = true; btn.textContent = "…";
                rendersStore.cancel(id)
                    .catch(() => {})
                    .finally(() => {
                        // Clear shortly after the next likely poll cycle so the
                        // row either disappears or returns to its normal state.
                        setTimeout(() => cancellingIds.delete(id), 3000);
                    });
                return;
            }
            armedCancels.add(id);
            btn.classList.add("armed");
            btn.textContent = "Cancel?";
            setTimeout(() => {
                armedCancels.delete(id);
                if (btn.isConnected) {
                    btn.classList.remove("armed");
                    btn.textContent = "✕";
                }
            }, 1500);
        });
    });
}

let subscribed = false;
function ensureRenderSubscription() {
    if (subscribed) return;
    subscribed = true;
    rendersStore.subscribe((s) => {
        latestSnap = s;
        const existing = document.querySelector("#sidebar .server-panel");
        if (existing) {
            const wrapper = document.createElement("div");
            wrapper.innerHTML = serverPanel(cachedStats, s).trim();
            const replacement = wrapper.firstElementChild;
            existing.replaceWith(replacement);
            wireServerPanel(replacement);
        }
    });
}

export async function renderSidebar(hash) {
  const root = document.getElementById("sidebar");
  if (!root) return;
  ensureRenderSubscription();
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

    ${serverPanel(cachedStats, latestSnap)}
  `;
  wireServerPanel(root.querySelector(".server-panel"));
}

// Allow other views (e.g. after creating/deleting a camera) to nuke the cache.
export function invalidateSidebar() {
  cachedCameras = null;
  cachedStats = null;
  lastFetch = 0;
}
