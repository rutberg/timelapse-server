// Camera detail — overview / frames / schedule / renders / settings tabs.

import {
  api, registerView, escapeHtml, icon, fmtNum, formatBytes,
  relativeTime, statusKind, renderTopbar, openModal
} from "/static/v2/app.js";
import { mountScheduleControl } from "/static/v2/components/schedule.js";
import { invalidateSidebar } from "/static/v2/components/sidebar.js";

function statusPill(status) {
  const kind = statusKind(status);
  if (kind === "live")   return `<span class="pill live"><span class="dot green pulse"></span>LIVE</span>`;
  if (kind === "paused") return `<span class="pill off"><span class="dot grey"></span>PAUSED</span>`;
  if (kind === "failed") return `<span class="pill bad"><span class="dot red"></span>ERROR</span>`;
  return `<span class="pill off"><span class="dot grey"></span>OFFLINE</span>`;
}

function nextAllowedHour(currentHour, captureHours) {
  const sorted = [...new Set(captureHours)].sort((a,b)=>a-b);
  for (const h of sorted) if (h > currentHour) return h;
  return sorted[0];
}

function scheduleSummary(status, config) {
  const mode = deriveScheduleMode(config);

  if (mode === "daylight") {
    if (status?.in_schedule === false) return "daylight only · waiting for sunrise";
    if (status?.in_schedule === true)  return "daylight only · active";
    return "daylight only";
  }

  if (mode === "scene") {
    const t = config?.light_threshold;
    const cur = status?.current_light;
    const tag = t != null ? `Y\u0304 \u2265 ${t}` : "luminance gated";
    if (cur != null && t != null) {
      return `${tag} \u00b7 now ${cur} \u00b7 ${cur >= t ? "capturing" : "skipped"}`;
    }
    return tag;
  }

  // hours mode
  const hours = config?.capture_hours;
  if (!hours || !hours.length) return "always (24/7)";
  if (status?.in_schedule === false && status?.local_hour != null) {
    const next = nextAllowedHour(status.local_hour, hours);
    return `paused until ${String(next).padStart(2,"0")}:00 (agent local)`;
  }
  if (status?.in_schedule === true) return `active \u00b7 ${hours.length} hours/day`;
  return `${hours.length} hours/day`;
}

function deriveScheduleMode(config) {
  if (config?.schedule_mode === "daylight" ||
      config?.schedule_mode === "hours" ||
      config?.schedule_mode === "scene") return config.schedule_mode;
  if (config?.light_threshold != null) return "scene";
  if (config?.capture_hours && config.capture_hours.length) return "hours";
  return "always";
}

// ---- Connection-strength block ---------------------------------------------

function rssiQuality(dbm) {
  if (dbm == null || !Number.isFinite(dbm)) return { bars: 0, label: "no signal", tone: "var(--soft)" };
  if (dbm >= -55) return { bars: 4, label: "excellent", tone: "var(--green)" };
  if (dbm >= -65) return { bars: 3, label: "good",      tone: "var(--green)" };
  if (dbm >= -75) return { bars: 2, label: "fair",      tone: "var(--accent-2)" };
  if (dbm >= -85) return { bars: 1, label: "weak",      tone: "var(--accent-2)" };
  return                  { bars: 0, label: "unusable", tone: "var(--red)" };
}

function renderConnectionBlock(status) {
  const q = rssiQuality(status?.signal_dbm);
  const bars = [1, 2, 3, 4].map((n) => `
    <span style="display:block;width:4px;border-radius:1px;height:${4 + n*3}px;
                 background:${n <= q.bars ? q.tone : "var(--border-strong)"}"></span>
  `).join("");
  return `
    <div class="row" style="margin-top:10px;gap:14px;align-items:center">
      <div style="display:flex;align-items:flex-end;gap:2px;height:16px">${bars}</div>
      <div class="col" style="gap:2px">
        <div style="font-size:15px;font-weight:500;color:${q.tone};text-transform:capitalize">${q.label}</div>
        <div class="mono small" style="color:var(--soft)">
          ${status?.signal_dbm != null ? `${status.signal_dbm} dBm` : "no RSSI"}
        </div>
      </div>
    </div>`;
}

async function renderCamera(root, hash) {
  const cameraId = decodeURIComponent(hash.replace(/^#\/cameras\/([^/]+).*/, "$1"));
  const tabMatch = hash.match(/^#\/cameras\/[^/]+\/(\w+)/);
  const tab = tabMatch ? tabMatch[1] : "overview";
  const encId = encodeURIComponent(cameraId);

  let camera = null;
  let pollTimer = null;
  let scheduleCtl = null;
  let scheduleDirty = null; // unsaved schedule edits
  const framesState = {
    archiveLoaded: false,
    mounted: false,
    months: [],
    days: [],
    monthOpen: new Set(),
    activeDay: "",
    daySummary: null,
    frames: [],
    selected: new Set(),
    lastSelectedCursor: null,
    groupBy: window.localStorage?.getItem(`frames:${cameraId}:groupBy`) || "hour",
    density: window.localStorage?.getItem(`frames:${cameraId}:density`) || "",
    cursor: null,
    loading: false,
    error: "",
    warping: false,
  };
  let frameMarquee = null;
  let frameMarqueeMoved = false;
  let frameMarqueeBase = null;
  let frameKeydownHandler = null;
  let frameMousemoveHandler = null;
  let frameMouseupHandler = null;
  let frameResizeHandler = null;

  // DSLR re-initialize progress: 'idle' | 'saving' | 'waiting' | 'done'
  let reinitPhase = 'idle';
  let reinitDoneAt = null;
  let reinitErrorMsg = null;

  async function load() {
    try {
      const data = await api.fetchJson("/api/cameras");
      camera = data.cameras.find(c => c.camera_id === cameraId);
      if (!camera) {
        renderTopbar([{label:"Dashboard",href:"#/dashboard"},"Unknown"], "");
        root.innerHTML = `<div class="empty"><h3>Camera not found</h3><div class="mono small">${escapeHtml(cameraId)}</div><p style="margin-top:16px"><a class="btn" href="#/dashboard">${icon("back",12)}Back to dashboard</a></p></div>`;
        return;
      }
      // Track DSLR re-initialize phase across polls so the status text persists.
      const dslrCfg0 = camera.config?.dslr || {};
      const dslrSt0 = camera.status?.dslr || {};
      const reinitTokenPending = dslrCfg0.reinit_token && dslrCfg0.reinit_token !== dslrSt0.last_reinit_token;
      if (reinitPhase === 'idle' && reinitTokenPending) {
        reinitPhase = 'waiting';
      } else if (reinitPhase === 'waiting' && !reinitTokenPending) {
        reinitPhase = 'done';
        reinitDoneAt = dslrSt0.last_init_at || new Date().toISOString();
      }
      // While the user is on the schedule tab, don't re-paint the body — that
      // would destroy the live schedule control mid-edit and silently revert
      // unsaved selections. Just push the latest light reading into the control.
      if (tab === "schedule" && scheduleCtl) {
        scheduleCtl.setCurrentLight(camera.status?.current_light);
        return;
      }
      if (tab === "frames" && framesState.mounted) {
        updateFrameToolbar();
        return;
      }
      paint();
    } catch (e) {
      root.innerHTML = `<div class="empty"><h3>Failed to load</h3><div class="mono small">${escapeHtml(e.message)}</div></div>`;
    }
  }

  function paint() {
    const display = camera.config?.display_name || camera.camera_id;
    renderTopbar([{label:"Dashboard",href:"#/dashboard"}, display], `
      <button class="btn primary sm" data-action="render">${icon("film",12)}Render</button>
    `);

    const tabs = ["overview","frames","schedule","renders","settings"];
    root.innerHTML = `
      <div class="tabs">
        ${tabs.map(t => `<a class="tab ${t===tab?"active":""}" href="#/cameras/${encId}/${t}">${t}</a>`).join("")}
      </div>
      <div id="cam-tab-body"></div>
    `;

    const body = document.getElementById("cam-tab-body");
    if (tab === "overview")  body.innerHTML = renderOverview();
    if (tab === "frames")    body.innerHTML = renderFramesTab();
    if (tab === "schedule")  body.innerHTML = renderScheduleTab();
    if (tab === "renders")   body.innerHTML = renderRendersTab();
    if (tab === "settings")  body.innerHTML = renderSettingsTab();

    if (tab === "schedule") wireSchedule();
    if (tab === "settings") wireSettings();
    if (tab === "renders") wireRenders();
    if (tab === "frames") wireFrames();

    root.querySelector('[data-action="render"]')?.addEventListener("click", async () => {
      const { openRenderModal } = await import("/static/v2/views/library.js");
      openRenderModal(cameraId);
    });
    root.querySelector('[data-action="toggle-pause"]')?.addEventListener("click", togglePause);
  }

  async function togglePause() {
    const next = { ...camera.config, enabled: !camera.config.enabled };
    try {
      camera.config = await api.fetchJson(`/api/cameras/${encId}/config`, { method:"PUT", body: JSON.stringify(next) });
      paint();
    } catch (e) { alert(e.message); }
  }

  function renderOverview() {
    const status = camera.status || {};
    const cfg = camera.config || {};
    const latestUrl = camera.latest_image
      ? `/api/cameras/${encId}/latest?ts=${encodeURIComponent(status.last_capture_at || Date.now())}`
      : null;
    return `
      <div style="padding:24px;display:grid;grid-template-columns:1fr 320px;gap:20px">
        <div>
          <div class="cam-canvas">
            ${latestUrl
              ? `<img src="${latestUrl}" alt="latest capture"/>`
              : `<div class="placeholder">No frames captured yet</div>`}
            <div class="cam-overlay-tl">${statusPill(status)}</div>
            ${status.last_capture_at
              ? `<div class="cam-overlay-br"><span class="stamp">${escapeHtml(status.last_capture_at.replace("T"," · ").slice(0,19))}</span></div>` : ""}
          </div>
          <div class="row" style="gap:8px;margin-top:12px">
            <button class="btn sm" data-reload-frame>${icon("refresh",12)}Refresh</button>
            ${latestUrl ? `<a class="btn sm" href="${latestUrl}" download>${icon("download",12)}Download frame</a>` : ""}
            <span class="mono small" style="margin-left:auto">Updated ${relativeTime(status.last_capture_at)}</span>
          </div>

          <div class="row" style="margin-top:14px;gap:10px;padding:10px 12px;background:var(--surface-2);border:1px solid var(--border);border-radius:8px">
            ${icon("clock",13)}
            <span class="mono small" style="color:var(--ink)">${escapeHtml(scheduleSummary(status, cfg))}</span>
            <a class="small" href="#/cameras/${encodeURIComponent(cameraId)}/schedule" style="margin-left:auto;color:var(--soft)">Edit schedule →</a>
          </div>

          ${status.last_error ? `
            <div class="banner bad" style="margin-top:14px">
              ${icon("alert",14)}
              <div class="grow">
                <strong>Last error</strong>
                <div class="mono small" style="color:var(--soft);margin-top:4px;word-break:break-word">${escapeHtml(status.last_error)}</div>
              </div>
            </div>` : ""}
        </div>

        <div class="col" style="gap:14px">
          <div class="card"><div class="card-b">
            <div class="between">
              <div class="lbl">Status</div>
              <button class="btn ghost sm" data-action="toggle-pause" title="${cfg.enabled?"Pause capture":"Resume capture"}">
                ${icon(cfg.enabled?"pause":"play",11)}${cfg.enabled?"Pause":"Resume"}
              </button>
            </div>
            <div class="row" style="margin-top:6px">
              <span class="dot ${statusKind(status)==="live"?"green pulse":statusKind(status)==="failed"?"red":"grey"}"></span>
              <span style="font-size:16px;font-weight:500">${statusKind(status).toUpperCase()}</span>
            </div>
            <div style="height:1px;background:var(--border);margin:14px 0"></div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
              <div><div class="lbl">Frames</div><div class="num" style="font-size:18px;margin-top:2px">${fmtNum(camera.image_count)}</div></div>
              <div><div class="lbl">Interval</div><div class="num" style="font-size:18px;margin-top:2px">${cfg.interval_seconds ? Math.round(cfg.interval_seconds/60) + " min" : "—"}</div></div>
              <div><div class="lbl">Pending</div><div class="num" style="font-size:18px;margin-top:2px">${fmtNum(status.pending_count||0)}</div></div>
              <div><div class="lbl">Queue size</div><div class="num" style="font-size:18px;margin-top:2px">${formatBytes(status.pending_bytes||0)}</div></div>
            </div>
          </div></div>

          <div class="card"><div class="card-b">
            <div class="lbl">Connection</div>
            ${renderConnectionBlock(status)}
            <div class="mono small" style="margin-top:10px;padding-top:10px;border-top:1px solid var(--border);color:var(--soft)">
              <div>${escapeHtml(status.source_ip||status.hostname||camera.hostname||"\u2014")} \u00b7 agent ${escapeHtml(status.agent_version||"\u2014")}</div>
              <div style="margin-top:2px">Last heartbeat: ${relativeTime(status.last_seen)}</div>
              ${status.local_hour != null ? `<div style="margin-top:2px">Agent local: ${String(status.local_hour).padStart(2,"0")}:00</div>` : ""}
            </div>
          </div></div>

          <div class="card"><div class="card-b">
            <div class="lbl">Quick render</div>
            <div class="col" style="gap:8px;margin-top:8px">
              <button class="btn" data-render-range="1">Last 24 hours${icon("arrow",12)}</button>
              <button class="btn" data-render-range="7">Last 7 days${icon("arrow",12)}</button>
              <button class="btn" data-render-range="all">All time${icon("arrow",12)}</button>
            </div>
          </div></div>
        </div>
      </div>`;
  }

  function renderFramesTab() {
    return `
      <div class="frames-browser">
        <aside class="frames-rail">
          <div class="frames-rail-search">
            <input class="input" id="frames-search" placeholder="Search dates…" />
          </div>
          <div id="frames-archive-rail" class="frames-archive-rail">
            <div class="loading mono small">Loading archive…</div>
          </div>
        </aside>

        <section class="frames-main">
          <div class="frames-toolbar">
            <div>
              <div class="lbl">Frames</div>
              <div class="frames-day-title" id="frames-day-title">Loading…</div>
              <div class="small" id="frames-day-sub">Preparing archive</div>
            </div>
            <div class="row frames-toolbar-controls">
              <div class="seg" id="frames-group-by">
                ${["hour","day","week"].map((value) => `<button data-group="${value}" class="${framesState.groupBy === value ? "active" : ""}">${value}</button>`).join("")}
              </div>
              <div class="seg" id="frames-density">
                <button data-density="dense" class="${framesState.density === "dense" ? "active" : ""}">S</button>
                <button data-density="" class="${framesState.density === "" ? "active" : ""}">M</button>
                <button data-density="large" class="${framesState.density === "large" ? "active" : ""}">L</button>
              </div>
              <button class="btn sm" id="frames-warp-toggle">${icon("play",11)}Time-warp</button>
              <button class="btn sm" id="frames-refresh">${icon("refresh",12)}Refresh</button>
            </div>
          </div>

          <div class="frames-scrub-head">
            <div class="between" style="align-items:baseline;margin-bottom:8px">
              <div class="mono" id="frames-scrub-title">${escapeHtml(framesState.activeDay || "—")}</div>
              <div class="lbl">Click scrubber to jump · arrows scrub hours</div>
            </div>
            <div class="frames-scrubber" id="frames-scrubber">
              <div class="frames-scrubber-density" id="frames-scrubber-density"></div>
              <div class="frames-scrubber-hours">${Array.from({length: 8}, (_, i) => `<span>${String(i * 3).padStart(2, "0")}</span>`).join("")}</div>
              <div class="frames-scrubber-window" id="frames-scrubber-window"></div>
              <div class="frames-warp-cursor" id="frames-warp-cursor"></div>
              <div class="frames-warp-preview" id="frames-warp-preview"></div>
            </div>
          </div>

          <div class="frames-grid-scroll" id="frames-grid-scroll">
            <div class="loading mono small">Loading frames…</div>
          </div>

          <div class="frames-selection-bar" id="frames-selection-bar">
            <span><span class="num" id="frames-selection-count">0</span> selected</span>
            <span class="mono" id="frames-selection-range">—</span>
            <span class="frames-selection-sep"></span>
            <button type="button" data-render-selection>Render clip</button>
            <button type="button" class="danger-dark" data-delete-selection>Delete</button>
            <button type="button" data-clear-selection>Clear</button>
          </div>
        </section>

        <div class="frames-lightbox" id="frames-lightbox" aria-hidden="true"></div>
      </div>`;
  }

  function wireFrames() {
    removeFrameGlobalListeners();
    framesState.mounted = true;
    root.querySelector("#frames-group-by")?.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-group]");
      if (!button) return;
      framesState.groupBy = button.dataset.group;
      window.localStorage?.setItem(`frames:${cameraId}:groupBy`, framesState.groupBy);
      paintFrames();
    });
    root.querySelector("#frames-density")?.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-density]");
      if (!button) return;
      framesState.density = button.dataset.density;
      window.localStorage?.setItem(`frames:${cameraId}:density`, framesState.density);
      paintFrames();
    });
    root.querySelector("#frames-search")?.addEventListener("input", paintFrameRail);
    root.querySelector("#frames-refresh")?.addEventListener("click", () => loadFrameArchive({ keepDay: true }));
    root.querySelector("#frames-warp-toggle")?.addEventListener("click", toggleFrameWarp);
    root.querySelector("#frames-scrubber")?.addEventListener("mousemove", updateFrameWarpPreview);
    root.querySelector("#frames-scrubber")?.addEventListener("mouseleave", () => {
      const preview = root.querySelector("#frames-warp-preview");
      if (preview) preview.innerHTML = "";
    });
    root.querySelector("#frames-scrubber")?.addEventListener("click", scrubFramesToEvent);
    root.querySelector("#frames-grid-scroll")?.addEventListener("scroll", () => {
      window.requestAnimationFrame(updateFrameScrubberWindow);
    });
    root.querySelector("[data-clear-selection]")?.addEventListener("click", clearFrameSelection);
    root.querySelector("[data-delete-selection]")?.addEventListener("click", deleteSelectedFrames);
    root.querySelector("[data-render-selection]")?.addEventListener("click", async () => {
      const { openRenderModal } = await import("/static/v2/views/library.js");
      openRenderModal(cameraId);
    });

    root.querySelector("#frames-grid-scroll")?.addEventListener("mousedown", startFrameMarquee);
    frameMousemoveHandler = moveFrameMarquee;
    frameMouseupHandler = endFrameMarquee;
    frameKeydownHandler = handleFrameKeyboard;
    frameResizeHandler = updateFrameScrubberWindow;
    window.addEventListener("mousemove", frameMousemoveHandler);
    window.addEventListener("mouseup", frameMouseupHandler);
    window.addEventListener("keydown", frameKeydownHandler);
    window.addEventListener("resize", frameResizeHandler);

    if (!framesState.archiveLoaded && !framesState.loading) {
      loadFrameArchive();
    } else {
      paintFrameRail();
      paintFrames();
    }
  }

  function removeFrameGlobalListeners() {
    if (frameMousemoveHandler) window.removeEventListener("mousemove", frameMousemoveHandler);
    if (frameMouseupHandler) window.removeEventListener("mouseup", frameMouseupHandler);
    if (frameKeydownHandler) window.removeEventListener("keydown", frameKeydownHandler);
    if (frameResizeHandler) window.removeEventListener("resize", frameResizeHandler);
    frameMousemoveHandler = null;
    frameMouseupHandler = null;
    frameKeydownHandler = null;
    frameResizeHandler = null;
  }

  async function loadFrameArchive({ keepDay = false } = {}) {
    if (framesState.loading) return;
    framesState.loading = true;
    framesState.error = "";
    paintFrameLoading("Loading archive…");
    try {
      const data = await api.fetchJson(`/api/cameras/${encId}/frame-days`);
      framesState.days = data.days || [];
      framesState.months = data.months || [];
      framesState.archiveLoaded = true;
      if (!keepDay || !framesState.days.some(day => day.day === framesState.activeDay)) {
        framesState.activeDay = framesState.days[0]?.day || "";
      }
      if (framesState.activeDay) framesState.monthOpen.add(framesState.activeDay.slice(0, 7));
      paintFrameRail();
      if (framesState.activeDay) await loadFrameDay(framesState.activeDay);
      else {
        framesState.daySummary = null;
        framesState.frames = [];
        framesState.cursor = null;
        paintFrames();
      }
    } catch (e) {
      framesState.error = e.message;
      paintFrames();
    } finally {
      framesState.loading = false;
    }
  }

  async function loadFrameDay(day) {
    framesState.activeDay = day;
    framesState.daySummary = framesState.days.find(entry => entry.day === day) || null;
    framesState.selected.clear();
    framesState.lastSelectedCursor = null;
    framesState.frames = [];
    framesState.cursor = null;
    framesState.loading = true;
    framesState.error = "";
    paintFrames();
    try {
      const params = new URLSearchParams({ day, order: "asc", limit: "5000" });
      const data = await api.fetchJson(`/api/cameras/${encId}/frames?${params}`);
      framesState.frames = data.frames || [];
      framesState.cursor = data.next_cursor || null;
      framesState.monthOpen.add(day.slice(0, 7));
      paintFrameRail();
      paintFrames();
    } catch (e) {
      framesState.error = e.message;
      paintFrames();
    } finally {
      framesState.loading = false;
    }
  }

  function paintFrameLoading(message) {
    const grid = root.querySelector("#frames-grid-scroll");
    if (grid) grid.innerHTML = `<div class="loading mono small">${escapeHtml(message)}</div>`;
  }

  function paintFrameRail() {
    const rail = root.querySelector("#frames-archive-rail");
    if (!rail) return;
    const query = (root.querySelector("#frames-search")?.value || "").trim().toLowerCase();
    if (!framesState.days.length) {
      rail.innerHTML = `<div class="empty"><h3>No frames</h3><p>No captures have been uploaded yet.</p></div>`;
      return;
    }
    const daysByMonth = new Map();
    for (const day of framesState.days) {
      if (query && !day.day.toLowerCase().includes(query) && !formatDayShort(day.day).toLowerCase().includes(query)) continue;
      if (!daysByMonth.has(day.month)) daysByMonth.set(day.month, []);
      daysByMonth.get(day.month).push(day);
    }
    const railHtml = framesState.months.map((month) => {
      const days = daysByMonth.get(month.month) || [];
      if (query && !days.length) return "";
      const open = framesState.monthOpen.has(month.month);
      return `
        <div class="frames-month ${open ? "open" : ""}">
          <button class="frames-month-head" data-month="${escapeHtml(month.month)}" type="button">
            <span class="frames-chev">▶</span>
            <span class="frames-month-name">${escapeHtml(formatMonthLabel(month.month))}</span>
            <span class="frames-month-meta">${fmtNum(month.frame_count)} · ${month.day_count}d${month.gap_count ? ` · ${month.gap_count} gaps` : ""}</span>
          </button>
          <div class="frames-month-days">
            ${days.map(renderFrameDayRow).join("")}
          </div>
        </div>`;
    }).join("").trim();
    rail.innerHTML = railHtml || `<div class="empty"><h3>No matches</h3><p>No captured days match that search.</p></div>`;
    rail.querySelectorAll("[data-month]").forEach(button => {
      button.addEventListener("click", () => {
        const month = button.dataset.month;
        if (framesState.monthOpen.has(month)) framesState.monthOpen.delete(month);
        else framesState.monthOpen.add(month);
        paintFrameRail();
      });
    });
    rail.querySelectorAll("[data-frame-day]").forEach(button => {
      button.addEventListener("click", () => loadFrameDay(button.dataset.frameDay));
    });
  }

  function renderFrameDayRow(day) {
    const active = day.day === framesState.activeDay;
    return `
      <button class="frames-day-row ${active ? "active" : ""}" data-frame-day="${escapeHtml(day.day)}" type="button">
        <span class="frames-day-date">${escapeHtml(formatDayShort(day.day))}</span>
        <span class="frames-day-meta">
          <span>${fmtNum(day.count)}</span>
          ${day.gap_count ? `<span class="gap">${day.gap_count} gap${day.gap_count > 1 ? "s" : ""}</span>` : ""}
        </span>
      </button>`;
  }

  function paintFrames() {
    const grid = root.querySelector("#frames-grid-scroll");
    if (!grid) return;
    updateFrameToolbar();
    if (framesState.error) {
      grid.innerHTML = `<div class="banner bad">${icon("alert",14)}<div class="small">${escapeHtml(framesState.error)}</div></div>`;
      updateFrameSelectionUI();
      return;
    }
    if (framesState.loading && !framesState.frames.length) {
      grid.innerHTML = `<div class="loading mono small">Loading frames…</div>`;
      return;
    }
    if (!framesState.activeDay || !framesState.frames.length) {
      grid.innerHTML = `<div class="empty"><h3>No frames</h3><p>${framesState.activeDay ? "No captures for this day." : "This camera has not uploaded frames yet."}</p></div>`;
      updateFrameSelectionUI();
      return;
    }
    const groups = buildFrameGroups();
    grid.innerHTML = groups.map(renderFrameGroup).join("") + `<div class="frames-grid-tail"></div>`;
    grid.querySelectorAll("[data-frame-cursor]").forEach(tile => {
      tile.addEventListener("mousedown", event => event.stopPropagation());
      tile.addEventListener("click", event => selectFrameFromEvent(event, tile.dataset.frameCursor));
      tile.addEventListener("dblclick", () => openFrameLightbox(tile.dataset.frameCursor));
    });
    grid.querySelectorAll("[data-select-group]").forEach(button => {
      button.addEventListener("click", () => {
        const key = button.dataset.selectGroup;
        const group = groups.find(item => item.key === key);
        if (!group) return;
        for (const frame of group.items.filter(item => item.type === "frame").map(item => item.frame)) {
          framesState.selected.add(frame.cursor);
        }
        updateFrameSelectionUI();
      });
    });
    renderFrameScrubber();
    updateFrameSelectionUI();
    window.requestAnimationFrame(updateFrameScrubberWindow);
  }

  function updateFrameToolbar() {
    const title = root.querySelector("#frames-day-title");
    const sub = root.querySelector("#frames-day-sub");
    const scrubTitle = root.querySelector("#frames-scrub-title");
    if (title) title.textContent = framesState.activeDay ? formatDayTitle(framesState.activeDay) : "No frames";
    if (scrubTitle) scrubTitle.textContent = framesState.activeDay || "—";
    if (sub) {
      const summary = framesState.daySummary;
      sub.textContent = summary
        ? `${fmtNum(summary.count)} frames · ${formatDayRange(summary)}${summary.gap_count ? ` · ${summary.gap_count} gap${summary.gap_count > 1 ? "s" : ""}` : " · no gaps"}`
        : `${fmtNum(camera.image_count)} total frames`;
    }
    root.querySelectorAll("#frames-group-by button").forEach(button => button.classList.toggle("active", button.dataset.group === framesState.groupBy));
    root.querySelectorAll("#frames-density button").forEach(button => button.classList.toggle("active", button.dataset.density === framesState.density));
    root.querySelector("#frames-warp-toggle")?.classList.toggle("primary", framesState.warping);
  }

  function buildFrameGroups() {
    const gapsByAfter = new Map((framesState.daySummary?.gaps || []).map(gap => [gap.after, gap]));
    const groups = [];
    let current = null;
    for (const frame of framesState.frames) {
      const key = frameGroupKey(frame);
      if (!current || current.key !== key) {
        current = { key, label: frameGroupLabel(frame), minute: frameMinute(frame), items: [] };
        groups.push(current);
      }
      current.items.push({ type: "frame", frame });
      const gap = gapsByAfter.get(frame.cursor);
      if (gap) current.items.push({ type: "gap", gap });
    }
    return groups;
  }

  function renderFrameGroup(group) {
    let html = `
      <div class="frames-group-head" data-group-key="${escapeHtml(group.key)}" data-minute="${group.minute}">
        <span class="frames-group-title">${escapeHtml(group.label)}</span>
        <span class="frames-group-meta">${fmtNum(group.items.filter(item => item.type === "frame").length)} frames</span>
        <span class="frames-group-actions"><button class="btn ghost sm" data-select-group="${escapeHtml(group.key)}">Select</button></span>
      </div>`;
    let openGrid = false;
    const closeGrid = () => {
      if (openGrid) {
        html += `</div>`;
        openGrid = false;
      }
    };
    for (const item of group.items) {
      if (item.type === "frame") {
        if (!openGrid) {
          html += `<div class="frames-browser-grid ${escapeHtml(framesState.density)}">`;
          openGrid = true;
        }
        html += renderFrameTile(item.frame);
      } else {
        closeGrid();
        html += renderGapCard(item.gap);
      }
    }
    closeGrid();
    return html;
  }

  function renderFrameTile(frame) {
    const selected = framesState.selected.has(frame.cursor);
    const stamp = frameStamp(frame);
    return `
      <button class="frame-browser-tile ${selected ? "selected" : ""}" data-frame-cursor="${escapeHtml(frame.cursor)}" type="button">
        <img src="${escapeHtml(frame.url)}" alt="${escapeHtml(stamp)}" loading="lazy"/>
        <span class="frame-check"></span>
        <span class="frame-stamp">${escapeHtml(stamp)}</span>
      </button>`;
  }

  function renderGapCard(gap) {
    const height = Math.min(180, Math.max(54, Math.round(gap.duration_seconds / 45)));
    return `
      <div class="frames-gap-card" style="min-height:${height}px">
        <div class="frames-gap-mark">!</div>
        <div class="grow">
          <div class="frames-gap-title">Capture gap · ${escapeHtml(formatDuration(gap.duration_seconds))}</div>
          <div class="small">${escapeHtml(timeFromIso(gap.start))} → ${escapeHtml(timeFromIso(gap.end))} · no frames uploaded in this window</div>
        </div>
      </div>`;
  }

  function renderFrameScrubber() {
    const density = root.querySelector("#frames-scrubber-density");
    if (!density) return;
    const counts = new Array(24).fill(0);
    for (const frame of framesState.frames) counts[frameHour(frame)] += 1;
    const max = Math.max(1, ...counts);
    const bars = counts.map((count, hour) => {
      if (!count) return "";
      return `<span class="frames-scrubber-bar" style="left:${(hour / 24) * 100}%;width:${(100 / 24) - 0.2}%;height:${Math.max(10, (count / max) * 100)}%"></span>`;
    }).join("");
    const gaps = (framesState.daySummary?.gaps || []).map(gap => {
      const start = minuteFromIso(gap.start);
      const end = minuteFromIso(gap.end);
      return `<span class="frames-scrubber-gap" style="left:${(start / 1440) * 100}%;width:${Math.max(0.3, ((end - start) / 1440) * 100)}%"></span>`;
    }).join("");
    density.innerHTML = bars + gaps;
  }

  function updateFrameScrubberWindow() {
    const scroller = root.querySelector("#frames-grid-scroll");
    const win = root.querySelector("#frames-scrubber-window");
    if (!scroller || !win) return;
    const maxScroll = scroller.scrollHeight - scroller.clientHeight;
    if (maxScroll <= 0) {
      win.style.left = "0%";
      win.style.width = "100%";
      return;
    }
    const ratio = scroller.scrollTop / maxScroll;
    const visibleRatio = Math.min(1, scroller.clientHeight / scroller.scrollHeight);
    win.style.left = `${Math.min(100, ratio * 100)}%`;
    win.style.width = `${Math.max(3, visibleRatio * 100)}%`;
  }

  function toggleFrameWarp() {
    framesState.warping = !framesState.warping;
    root.querySelector("#frames-scrubber")?.classList.toggle("warping", framesState.warping);
    updateFrameToolbar();
  }

  function updateFrameWarpPreview(event) {
    if (!framesState.warping || !framesState.frames.length) return;
    const scrubber = root.querySelector("#frames-scrubber");
    const cursor = root.querySelector("#frames-warp-cursor");
    const preview = root.querySelector("#frames-warp-preview");
    if (!scrubber || !cursor || !preview) return;
    const rect = scrubber.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
    const frame = nearestFrameByMinute(ratio * 1440);
    cursor.style.left = `${ratio * 100}%`;
    preview.style.left = `${ratio * 100}%`;
    if (frame) {
      preview.innerHTML = `<img src="${escapeHtml(frame.url)}" alt="${escapeHtml(frameStamp(frame))}"/><div class="mono">${escapeHtml(frameStamp(frame))}</div>`;
    }
  }

  function scrubFramesToEvent(event) {
    const scrubber = root.querySelector("#frames-scrubber");
    const scroller = root.querySelector("#frames-grid-scroll");
    if (!scrubber || !scroller) return;
    const rect = scrubber.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
    const hour = Math.floor(ratio * 24);
    const group = [...root.querySelectorAll(".frames-group-head")]
      .find(head => head.dataset.groupKey === String(hour));
    if (group) group.scrollIntoView({ behavior: "smooth", block: "start" });
    else scroller.scrollTo({ top: ratio * (scroller.scrollHeight - scroller.clientHeight), behavior: "smooth" });
  }

  function nearestFrameByMinute(minute) {
    let nearest = null;
    let best = Infinity;
    for (const frame of framesState.frames) {
      const distance = Math.abs(frameMinute(frame) - minute);
      if (distance < best) {
        best = distance;
        nearest = frame;
      }
    }
    return nearest;
  }

  function selectFrameFromEvent(event, cursor) {
    const index = framesState.frames.findIndex(frame => frame.cursor === cursor);
    if (index < 0) return;
    if (event.shiftKey && framesState.lastSelectedCursor) {
      const start = framesState.frames.findIndex(frame => frame.cursor === framesState.lastSelectedCursor);
      if (start < 0) {
        framesState.selected.clear();
        framesState.selected.add(cursor);
        framesState.lastSelectedCursor = cursor;
        updateFrameSelectionUI();
        return;
      }
      const a = Math.min(start, index);
      const b = Math.max(start, index);
      for (const frame of framesState.frames.slice(a, b + 1)) framesState.selected.add(frame.cursor);
    } else if (event.metaKey || event.ctrlKey) {
      if (framesState.selected.has(cursor)) framesState.selected.delete(cursor);
      else framesState.selected.add(cursor);
      framesState.lastSelectedCursor = cursor;
    } else {
      framesState.selected.clear();
      framesState.selected.add(cursor);
      framesState.lastSelectedCursor = cursor;
    }
    updateFrameSelectionUI();
  }

  function updateFrameSelectionUI() {
    root.querySelectorAll(".frame-browser-tile").forEach(tile => {
      tile.classList.toggle("selected", framesState.selected.has(tile.dataset.frameCursor));
    });
    const bar = root.querySelector("#frames-selection-bar");
    const count = root.querySelector("#frames-selection-count");
    const range = root.querySelector("#frames-selection-range");
    if (!bar || !count || !range) return;
    const selectedFrames = framesState.frames.filter(frame => framesState.selected.has(frame.cursor));
    bar.classList.toggle("show", selectedFrames.length > 0);
    count.textContent = String(selectedFrames.length);
    if (selectedFrames.length) {
      const first = selectedFrames[0];
      const last = selectedFrames[selectedFrames.length - 1];
      range.textContent = `${frameStamp(first)} → ${frameStamp(last)} · ${formatBytes(selectedFrames.reduce((sum, frame) => sum + (frame.size_bytes || 0), 0))}`;
    } else {
      range.textContent = "—";
    }
  }

  function clearFrameSelection() {
    framesState.selected.clear();
    framesState.lastSelectedCursor = null;
    updateFrameSelectionUI();
  }

  function startFrameMarquee(event) {
    if (event.button !== 0 || event.target.closest(".frame-browser-tile, button, a, input, .frames-group-head, .frames-gap-card")) return;
    frameMarquee = { startX: event.clientX, startY: event.clientY };
    frameMarqueeMoved = false;
    frameMarqueeBase = (event.shiftKey || event.metaKey || event.ctrlKey) ? new Set(framesState.selected) : new Set();
    let marquee = document.getElementById("frames-marquee");
    if (!marquee) {
      marquee = document.createElement("div");
      marquee.id = "frames-marquee";
      marquee.className = "frames-marquee";
      document.body.appendChild(marquee);
    }
    marquee.style.display = "block";
    marquee.style.left = `${event.clientX}px`;
    marquee.style.top = `${event.clientY}px`;
    marquee.style.width = "0px";
    marquee.style.height = "0px";
    event.preventDefault();
  }

  function moveFrameMarquee(event) {
    if (!frameMarquee) return;
    const dx = Math.abs(event.clientX - frameMarquee.startX);
    const dy = Math.abs(event.clientY - frameMarquee.startY);
    if (!frameMarqueeMoved && dx + dy < 4) return;
    frameMarqueeMoved = true;
    const x1 = Math.min(frameMarquee.startX, event.clientX);
    const y1 = Math.min(frameMarquee.startY, event.clientY);
    const x2 = Math.max(frameMarquee.startX, event.clientX);
    const y2 = Math.max(frameMarquee.startY, event.clientY);
    const marquee = document.getElementById("frames-marquee");
    if (marquee) {
      marquee.style.left = `${x1}px`;
      marquee.style.top = `${y1}px`;
      marquee.style.width = `${x2 - x1}px`;
      marquee.style.height = `${y2 - y1}px`;
    }
    const next = new Set(frameMarqueeBase || []);
    root.querySelectorAll(".frame-browser-tile").forEach(tile => {
      const rect = tile.getBoundingClientRect();
      if (rect.right >= x1 && rect.left <= x2 && rect.bottom >= y1 && rect.top <= y2) {
        next.add(tile.dataset.frameCursor);
      }
    });
    framesState.selected = next;
    updateFrameSelectionUI();
  }

  function endFrameMarquee() {
    if (!frameMarquee) return;
    const marquee = document.getElementById("frames-marquee");
    if (marquee) marquee.style.display = "none";
    if (!frameMarqueeMoved) clearFrameSelection();
    frameMarquee = null;
    frameMarqueeMoved = false;
    frameMarqueeBase = null;
  }

  async function deleteSelectedFrames() {
    const frames = framesState.frames.filter(frame => framesState.selected.has(frame.cursor));
    if (!frames.length) return;
    if (!confirm(`Delete ${frames.length} selected frame${frames.length === 1 ? "" : "s"}? This cannot be undone.`)) return;
    try {
      for (const frame of frames) {
        await api.fetchJson(`/api/cameras/${encId}/frames/${encodeURIComponent(frame.day)}/${encodeURIComponent(frame.filename)}`, { method: "DELETE" });
      }
      await loadFrameArchive({ keepDay: true });
    } catch (e) {
      alert(`Delete failed: ${e.message}`);
    }
  }

  function openFrameLightbox(cursor) {
    const index = framesState.frames.findIndex(frame => frame.cursor === cursor);
    if (index < 0) return;
    paintFrameLightbox(index);
  }

  function paintFrameLightbox(index) {
    const frame = framesState.frames[index];
    const box = root.querySelector("#frames-lightbox");
    if (!frame || !box) return;
    box.dataset.index = String(index);
    box.classList.add("open");
    box.setAttribute("aria-hidden", "false");
    const nearby = framesState.frames.slice(Math.max(0, index - 8), index + 9);
    box.innerHTML = `
      <button class="frames-lb-close" type="button">ESC ${icon("x",12)}</button>
      <button class="frames-lb-arrow prev" type="button">‹</button>
      <img class="frames-lb-img" src="${escapeHtml(frame.url)}" alt="${escapeHtml(frameStamp(frame, true))}"/>
      <button class="frames-lb-arrow next" type="button">›</button>
      <div class="frames-lb-strip">
        ${nearby.map(item => `<button class="frames-lb-thumb ${item.cursor === frame.cursor ? "active" : ""}" data-lb-cursor="${escapeHtml(item.cursor)}" type="button"><img src="${escapeHtml(item.url)}" alt="${escapeHtml(frameStamp(item))}"/></button>`).join("")}
      </div>
      <div class="frames-lb-meta">
        <div>
          <div class="frames-lb-title">${escapeHtml(frameStamp(frame, true))}</div>
          <div class="small">${escapeHtml(frame.day)} · ${formatBytes(frame.size_bytes)} · frame ${index + 1} / ${framesState.frames.length}</div>
        </div>
        <div class="row">
          <a class="btn sm" href="${escapeHtml(frame.url)}" download="${escapeHtml(frame.filename)}">${icon("download",12)}Download</a>
          <a class="btn primary sm" href="${escapeHtml(frame.url)}" target="_blank" rel="noopener">${icon("ext",12)}Open</a>
        </div>
      </div>`;
    box.querySelector(".frames-lb-close")?.addEventListener("click", closeFrameLightbox);
    box.querySelector(".frames-lb-arrow.prev")?.addEventListener("click", () => navigateFrameLightbox(-1));
    box.querySelector(".frames-lb-arrow.next")?.addEventListener("click", () => navigateFrameLightbox(1));
    box.querySelectorAll("[data-lb-cursor]").forEach(button => button.addEventListener("click", () => openFrameLightbox(button.dataset.lbCursor)));
  }

  function closeFrameLightbox() {
    const box = root.querySelector("#frames-lightbox");
    if (!box) return;
    box.classList.remove("open");
    box.setAttribute("aria-hidden", "true");
    box.innerHTML = "";
  }

  function navigateFrameLightbox(delta) {
    const box = root.querySelector("#frames-lightbox");
    const index = Number(box?.dataset.index || 0);
    const next = Math.max(0, Math.min(framesState.frames.length - 1, index + delta));
    paintFrameLightbox(next);
  }

  function handleFrameKeyboard(event) {
    if (tab !== "frames") return;
    const tag = event.target?.tagName || "";
    if (tag === "INPUT" || tag === "TEXTAREA") return;
    const lightbox = root.querySelector("#frames-lightbox.open");
    if (lightbox) {
      if (event.key === "Escape") closeFrameLightbox();
      if (event.key === "ArrowLeft") navigateFrameLightbox(-1);
      if (event.key === "ArrowRight") navigateFrameLightbox(1);
      return;
    }
    if (event.key === "Escape") {
      clearFrameSelection();
      return;
    }
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      event.preventDefault();
      scrubFrameKeyboard(event.key === "ArrowLeft" ? -1 : 1, event.shiftKey);
    }
  }

  function scrubFrameKeyboard(direction, big) {
    const scroller = root.querySelector("#frames-grid-scroll");
    if (!scroller) return;
    if (big) {
      scroller.scrollBy({ top: direction * scroller.clientHeight * 0.9, behavior: "smooth" });
      return;
    }
    const heads = [...root.querySelectorAll(".frames-group-head")];
    if (!heads.length) return;
    const top = scroller.getBoundingClientRect().top;
    let current = 0;
    for (let i = 0; i < heads.length; i++) {
      if (heads[i].getBoundingClientRect().top - top <= 4) current = i;
      else break;
    }
    const next = Math.max(0, Math.min(heads.length - 1, current + direction));
    heads[next].scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function frameGroupKey(frame) {
    if (framesState.groupBy === "day") return framesState.activeDay;
    if (framesState.groupBy === "week") return "week";
    return String(frameHour(frame));
  }

  function frameGroupLabel(frame) {
    if (framesState.groupBy === "day") return formatDayTitle(framesState.activeDay);
    if (framesState.groupBy === "week") return `Week of ${formatDayShort(framesState.activeDay)}`;
    return `${String(frameHour(frame)).padStart(2, "0")}:00`;
  }

  function frameHour(frame) {
    return Math.floor(frameMinute(frame) / 60);
  }

  function frameMinute(frame) {
    const time = (frame.captured_at || "").split("T")[1] || "";
    const hour = Number(time.slice(0, 2));
    const minute = Number(time.slice(3, 5));
    if (Number.isFinite(hour) && Number.isFinite(minute)) return hour * 60 + minute;
    return 0;
  }

  function minuteFromIso(value) {
    const time = (value || "").split("T")[1] || "";
    const hour = Number(time.slice(0, 2));
    const minute = Number(time.slice(3, 5));
    if (Number.isFinite(hour) && Number.isFinite(minute)) return hour * 60 + minute;
    return 0;
  }

  function frameStamp(frame, includeDate = false) {
    if (!frame?.captured_at) return frame?.filename || "—";
    const text = frame.captured_at.replace("T", " ");
    return includeDate ? text : text.slice(11, 16);
  }

  function timeFromIso(value) {
    return (value || "").split("T")[1]?.slice(0, 5) || "—";
  }

  function formatDayShort(day) {
    const date = new Date(`${day}T00:00:00`);
    return date.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
  }

  function formatDayTitle(day) {
    const date = new Date(`${day}T00:00:00`);
    return date.toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric", year: "numeric" });
  }

  function formatMonthLabel(month) {
    const date = new Date(`${month}-01T00:00:00`);
    return date.toLocaleDateString("en-US", { month: "long", year: "numeric" });
  }

  function formatDayRange(summary) {
    if (!summary?.first_captured_at || !summary?.last_captured_at) return "—";
    return `${timeFromIso(summary.first_captured_at)} → ${timeFromIso(summary.last_captured_at)}`;
  }

  function formatDuration(seconds) {
    if (seconds < 60) return `${seconds}s`;
    if (seconds < 3600) return `${Math.round(seconds / 60)} min`;
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.round((seconds % 3600) / 60);
    return minutes ? `${hours}h ${minutes}m` : `${hours}h`;
  }

  function renderScheduleTab() {
    return `
      <div style="padding:24px;max-width:900px">
        <div id="schedule-host"></div>
        <div class="row" style="margin-top:14px;gap:8px">
          <button class="btn primary" data-save-schedule>Save schedule</button>
          <button class="btn ghost" data-revert-schedule>Revert</button>
          <span class="small" id="schedule-msg" style="margin-left:auto"></span>
        </div>
      </div>`;
  }

  function wireSchedule() {
    const host = document.getElementById("schedule-host");
    scheduleCtl = mountScheduleControl(host, {
      value: scheduleValue(),
      onChange: (v) => { scheduleDirty = v; },
    });
    document.querySelector("[data-save-schedule]").addEventListener("click", async () => {
      const v = scheduleCtl.getValue();
      try {
        camera.config = await api.fetchJson(`/api/cameras/${encId}/config`, {
          method: "PUT",
          body: JSON.stringify({
            ...camera.config,
            schedule_mode:   v.schedule_mode,
            capture_hours:   v.capture_hours,
            schedule_days:   v.schedule_days,
            light_threshold: v.light_threshold,
          }),
        });
        scheduleDirty = null;
        const msg = document.getElementById("schedule-msg");
        msg.textContent = "Saved.";
        msg.style.color = "var(--green)";
      } catch (e) {
        const msg = document.getElementById("schedule-msg");
        msg.textContent = e.message;
        msg.style.color = "var(--red)";
      }
    });
    document.querySelector("[data-revert-schedule]").addEventListener("click", () => {
      scheduleCtl.setValue(scheduleValue());
      scheduleDirty = null;
    });
  }

  function scheduleValue() {
    return {
      schedule_mode:   camera.config.schedule_mode,
      capture_hours:   camera.config.capture_hours,
      schedule_days:   camera.config.schedule_days,
      light_threshold: camera.config.light_threshold,
      current_light:   camera.status?.current_light,
    };
  }

  function renderRendersTab() {
    return `
      <div style="padding:24px">
        <div class="between">
          <div>
            <div class="lbl">Renders for this camera</div>
            <div class="small" style="margin-top:6px">Generated MP4s and GIFs.</div>
          </div>
          <button class="btn primary" data-action="render">${icon("film",12)}New render</button>
        </div>
        <div id="renders-list" style="margin-top:16px">
          <div class="loading mono small">Loading…</div>
        </div>
      </div>`;
  }

  async function wireRenders() {
    const list = document.getElementById("renders-list");
    if (!list) return;
    try {
      const data = await api.fetchJson(`/api/cameras/${encId}/videos`);
      paintRendersList(data.videos || []);
    } catch (e) {
      list.innerHTML = `<div class="banner bad">${icon("alert",14)}<div class="small">${escapeHtml(e.message)}</div></div>`;
    }
  }

  function paintRendersList(videos) {
    const list = document.getElementById("renders-list");
    if (!list) return;
    if (videos.length === 0) {
      list.innerHTML = `<div class="empty"><h3>No renders yet</h3><p>Click "New render" to make one from the captured frames.</p></div>`;
      return;
    }
    list.innerHTML = videos.map(v => renderCard(v)).join("");
    list.querySelectorAll("[data-preview]").forEach(btn => {
      btn.addEventListener("click", () => {
        const card = btn.closest(".render-card");
        const slot = card.querySelector(".preview-slot");
        if (slot.dataset.open === "1") {
          slot.innerHTML = "";
          slot.dataset.open = "0";
          btn.textContent = "Preview";
        } else {
          const filename = btn.dataset.preview;
          const url = `/api/cameras/${encId}/videos/${encodeURIComponent(filename)}`;
          slot.innerHTML = filename.endsWith(".gif")
            ? `<img src="${url}" alt="${escapeHtml(filename)}" style="max-width:100%;border-radius:4px;border:1px solid var(--border)"/>`
            : `<video src="${url}" controls style="width:100%;border-radius:4px;border:1px solid var(--border)"></video>`;
          slot.dataset.open = "1";
          btn.textContent = "Hide";
        }
      });
    });
    list.querySelectorAll("[data-delete]").forEach(btn => {
      btn.addEventListener("click", async () => {
        const filename = btn.dataset.delete;
        if (!confirm(`Delete ${filename}? This cannot be undone.`)) return;
        try {
          await api.fetchJson(`/api/cameras/${encId}/videos/${encodeURIComponent(filename)}`, { method: "DELETE" });
          await wireRenders();
        } catch (e) {
          alert(`Delete failed: ${e.message}`);
        }
      });
    });
  }

  function renderCard(v) {
    const url = `/api/cameras/${encId}/videos/${encodeURIComponent(v.filename)}`;
    return `
      <div class="card render-card" style="margin-bottom:10px">
        <div class="card-b">
          <div class="between">
            <div class="grow" style="min-width:0">
              <div style="font-weight:500;font-size:13px;font-family:var(--mono);word-break:break-all">${escapeHtml(v.filename)}</div>
              <div class="row" style="margin-top:4px;gap:10px">
                <span class="pill ${v.format === "gif" ? "warn" : ""}">${v.format.toUpperCase()}</span>
                <span class="mono small">${formatBytes(v.size_bytes)}</span>
                <span class="mono small">${relativeTime(v.created_at)}</span>
              </div>
            </div>
            <div class="row" style="gap:6px;flex-shrink:0">
              <button class="btn sm" data-preview="${escapeHtml(v.filename)}">Preview</button>
              <a class="btn sm" href="${url}" download="${escapeHtml(v.filename)}">${icon("download",12)}Download</a>
              <button class="btn sm danger" data-delete="${escapeHtml(v.filename)}">${icon("trash",12)}Delete</button>
            </div>
          </div>
          <div class="preview-slot" data-open="0" style="margin-top:10px"></div>
        </div>
      </div>`;
  }

  const DSLR_DEFAULTS = {
    shutterspeed: ['bulb','30','25','20','15','13','10','8','6','5','4','3.2','2.5','2','1.6','1.3','1','0.8','0.6','0.5','0.4','0.3','1/4','1/5','1/6','1/8','1/10','1/13','1/15','1/20','1/25','1/30','1/40','1/50','1/60','1/80','1/100','1/125','1/160','1/200','1/250','1/320','1/400','1/500','1/640','1/800','1/1000','1/1250','1/1600','1/2000','1/2500','1/3200','1/4000','1/5000','1/6400','1/8000'],
    aperture: ['1.2','1.4','1.6','1.8','2','2.2','2.5','2.8','3.2','3.5','4','4.5','5','5.6','6.3','7.1','8','9','10','11','13','14','16','18','20','22'],
    iso: ['Auto','100','125','160','200','250','320','400','500','640','800','1000','1250','1600','2000','2500','3200','4000','5000','6400','8000','10000','12800','25600','51200','102400'],
    exposurecompensation: ['-3','-2.6667','-2.3333','-2','-1.6667','-1.3333','-1','-0.6667','-0.3333','0','0.3333','0.6667','1','1.3333','1.6667','2','2.3333','2.6667','3'],
    whitebalance: ['Auto','Daylight','Cloudy','Tungsten','Fluorescent','Flash','Custom','Shade','Color Temperature'],
    imageformat: ['Large Fine JPEG','Large Normal JPEG','Medium Fine JPEG','Small Fine JPEG','RAW','RAW + Large Fine JPEG','cRAW','cRAW + Large Fine JPEG'],
  };

  const DSLR_INIT_CHOICES = {
    capturetarget: ['Internal RAM','Memory card'],
    drivemode: ['Single','Continuous','Self Timer 2 sec','Self Timer 10 sec'],
    focusmode: ['Manual','One Shot','AI Servo','Single','Servo'],
  };

  function dslrSelect(id, label, gphotoKey, choices, selected) {
    const opts = ['', ...choices].map(v =>
      `<option value="${escapeHtml(v)}" ${v === (selected || '') ? 'selected' : ''}>${escapeHtml(v) || '— (leave as-is)'}</option>`
    ).join('');
    return `<label class="field"><span class="lbl">${label}</span><select class="input" id="${id}">${opts}</select></label>`;
  }

  function reinitStatusHtml() {
    if (reinitPhase === 'saving')  return `<span style="color:var(--soft)">Saving…</span>`;
    if (reinitPhase === 'waiting') return `<span style="color:var(--soft)">Waiting for agent…</span>`;
    if (reinitPhase === 'done') {
      const when = reinitDoneAt ? relativeTime(reinitDoneAt) : 'just now';
      return `<span style="color:var(--green,#22c55e)">Done — re-initialized ${escapeHtml(when)}</span>`;
    }
    if (reinitErrorMsg) return `<span style="color:var(--red)">${escapeHtml(reinitErrorMsg)}</span>`;
    return '';
  }

  function renderDslrSection(cfg, status) {
    const dslrCfg = cfg.dslr || {};
    const dslrSt = status && status.dslr || {};
    const choices = dslrSt.choices || {};
    const currentValues = dslrSt.current_values || {};
    const expMode = dslrSt.exposure_mode || '—';
    const expBadge = expMode === 'M'
      ? `<span style="color:var(--green,#22c55e)">${escapeHtml(expMode)}</span>`
      : `<span style="color:var(--amber,#f59e0b)">${escapeHtml(expMode)} — manual mode recommended</span>`;
    const reinitInFlight = reinitPhase === 'saving' || reinitPhase === 'waiting';

    // Pre-select live current value when present, fall back to saved config, then default.
    const sel = (gphotoKey, field) => currentValues[gphotoKey] || dslrCfg[field] || '';
    const initOpts = (gphotoKey, field, def) => DSLR_INIT_CHOICES[gphotoKey].map(v =>
      `<option value="${escapeHtml(v)}" ${v === (currentValues[gphotoKey] || dslrCfg[field] || def) ? 'selected' : ''}>${escapeHtml(v)}</option>`
    ).join('');

    // Battery / available shots / shutter count are Canon-only PTP properties; on
    // Sony bodies these fields stay null. Rather than render permanent em-dashes
    // we only show each row when the body actually reports a value.
    const statTile = (label, value) => value == null
      ? ''
      : `<div><div class="lbl" style="font-size:11px">${label}</div>${value}</div>`;
    const tiles = [
      statTile('Battery', dslrSt.battery_level ? escapeHtml(dslrSt.battery_level) : null),
      statTile('Available shots', dslrSt.available_shots != null ? Number(dslrSt.available_shots).toLocaleString() : null),
      statTile('Shutter count', dslrSt.shutter_counter != null ? Number(dslrSt.shutter_counter).toLocaleString() : null),
    ].filter(Boolean).join('');
    const camera = dslrSt.lens_name || dslrSt.camera_model || '—';
    const cameraLabel = dslrSt.lens_name ? 'Lens' : 'Camera';
    const lastInit = dslrSt.last_init_at ? relativeTime(dslrSt.last_init_at) : '—';

    return `
      <div class="card" style="margin-top:14px"><div class="card-b">
        <div class="lbl">DSLR Status</div>
        ${tiles ? `<div style="display:grid;grid-template-columns:repeat(${tiles.match(/<div>/g).length},1fr);gap:8px;margin-top:12px;font-size:13px">${tiles}</div>` : ''}
        <div style="display:grid;grid-template-columns:2fr 1fr;gap:8px;margin-top:10px;font-size:13px">
          <div><div class="lbl" style="font-size:11px">${cameraLabel}</div>${escapeHtml(camera)}</div>
          <div><div class="lbl" style="font-size:11px">Last initialized</div>${escapeHtml(lastInit)}</div>
        </div>
        <div style="margin-top:8px;font-size:13px"><span class="lbl" style="font-size:11px">Exposure mode</span> ${expBadge}</div>
      </div></div>

      <div class="card" style="margin-top:14px"><div class="card-b">
        <div class="lbl">Camera Initialization</div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:12px">
          <label class="field"><span class="lbl">Capture target</span>
            <select class="input" id="d-capturetarget">${initOpts('capturetarget','capture_target','Memory card')}</select>
          </label>
          <label class="field"><span class="lbl">Drive mode</span>
            <select class="input" id="d-drivemode">${initOpts('drivemode','drive_mode','Single')}</select>
          </label>
          <label class="field"><span class="lbl">Focus mode</span>
            <select class="input" id="d-focusmode">${initOpts('focusmode','focus_mode','Manual')}</select>
          </label>
        </div>
        <div style="margin-top:14px;display:flex;align-items:center;gap:10px">
          <button class="btn" data-reinit ${reinitInFlight ? 'disabled' : ''}>${reinitInFlight ? '⏳ Re-initializing…' : 'Re-initialize'}</button>
          <span class="small" id="reinit-msg">${reinitStatusHtml()}</span>
        </div>
      </div></div>

      <div class="card" style="margin-top:14px"><div class="card-b">
        <div class="lbl">Capture Settings</div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:12px">
          ${dslrSelect('d-shutterspeed','Shutter speed','shutterspeed',choices.shutterspeed||DSLR_DEFAULTS.shutterspeed,sel('shutterspeed','shutterspeed'))}
          ${dslrSelect('d-aperture','Aperture','aperture',choices.aperture||DSLR_DEFAULTS.aperture,sel('aperture','aperture'))}
          ${dslrSelect('d-iso','ISO','iso',choices.iso||DSLR_DEFAULTS.iso,sel('iso','iso'))}
          ${dslrSelect('d-expcomp','Exposure comp.','exposurecompensation',choices.exposurecompensation||DSLR_DEFAULTS.exposurecompensation,sel('exposurecompensation','exposure_compensation'))}
          ${dslrSelect('d-wb','White balance','whitebalance',choices.whitebalance||DSLR_DEFAULTS.whitebalance,sel('whitebalance','whitebalance'))}
          ${dslrSelect('d-fmt','Image format','imageformat',choices.imageformat||DSLR_DEFAULTS.imageformat,sel('imageformat','image_format'))}
        </div>
      </div></div>`;
  }

  function renderSettingsTab() {
    const cfg = camera.config || {};
    const isGphoto2 = cfg.camera_backend === 'gphoto2' || camera.status && camera.status.active_backend === 'gphoto2';
    const hide = isGphoto2 ? ' style="display:none"' : '';
    return `
      <div style="padding:24px;max-width:720px">
        <div class="card"><div class="card-b">
          <div class="lbl">Capture</div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:12px">
            <label class="field"><span class="lbl">Display name</span>
              <input class="input sans" id="s-name" value="${escapeHtml(cfg.display_name||"")}" placeholder="${escapeHtml(camera.camera_id)}"/>
            </label>
            <label class="field"><span class="lbl">Interval (seconds)</span>
              <input class="input" id="s-interval" type="number" min="30" max="86400" value="${cfg.interval_seconds||600}"/>
            </label>
            <label class="field"${hide}><span class="lbl">Width (px)</span>
              <input class="input" id="s-width" type="number" min="320" max="10000" value="${cfg.image_width||""}" placeholder="full"/>
            </label>
            <label class="field"${hide}><span class="lbl">Height (px)</span>
              <input class="input" id="s-height" type="number" min="240" max="10000" value="${cfg.image_height||""}" placeholder="full"/>
            </label>
            <label class="field"${hide}><span class="lbl">JPEG quality (1–100)</span>
              <input class="input" id="s-quality" type="number" min="1" max="100" value="${cfg.jpeg_quality||85}"/>
            </label>
            <label class="field"><span class="lbl">Desired agent version</span>
              <input class="input" id="s-version" value="${escapeHtml(cfg.desired_agent_version||"")}" placeholder="latest"/>
            </label>
          </div>
        </div></div>

        ${isGphoto2 ? renderDslrSection(cfg, camera.status) : ''}

        <div class="row" style="margin-top:14px;gap:8px">
          <button class="btn primary" data-save-settings>Save settings</button>
          <span class="small" id="settings-msg"></span>
          <button class="btn danger" style="margin-left:auto" data-delete-camera>${icon("trash",12)}Delete camera</button>
        </div>
      </div>`;
  }

  function wireSettings() {
    const isGphoto2 = (camera.config && camera.config.camera_backend === 'gphoto2') || (camera.status && camera.status.active_backend === 'gphoto2');
    const optNum = (id) => { const el = document.getElementById(id); return el && el.value ? Number(el.value) : null; };
    const selVal = (id) => { const el = document.getElementById(id); return el ? el.value || null : null; };

    function buildDslrPayload(withReinit) {
      const existing = camera.config && camera.config.dslr || {};
      return {
        capture_target: selVal('d-capturetarget') || existing.capture_target || 'Memory card',
        drive_mode: selVal('d-drivemode') || existing.drive_mode || 'Single',
        focus_mode: selVal('d-focusmode') || existing.focus_mode || 'Manual',
        shutterspeed: selVal('d-shutterspeed'),
        aperture: selVal('d-aperture'),
        iso: selVal('d-iso'),
        exposure_compensation: selVal('d-expcomp'),
        whitebalance: selVal('d-wb'),
        image_format: selVal('d-fmt'),
        reinit_token: withReinit ? new Date().toISOString() : (existing.reinit_token || null),
      };
    }

    function buildBasePayload() {
      return {
        ...camera.config,
        display_name: document.getElementById("s-name").value.trim() || null,
        interval_seconds: Number(document.getElementById("s-interval").value),
        image_width:  optNum("s-width"),
        image_height: optNum("s-height"),
        jpeg_quality: Number(document.getElementById("s-quality").value) || 85,
        desired_agent_version: document.getElementById("s-version").value.trim() || null,
      };
    }

    document.querySelector("[data-save-settings]").addEventListener("click", async () => {
      const payload = {
        ...buildBasePayload(),
        ...(isGphoto2 ? { dslr: buildDslrPayload(false) } : {}),
      };
      const msg = document.getElementById("settings-msg");
      try {
        camera.config = await api.fetchJson(`/api/cameras/${encId}/config`, { method:"PUT", body: JSON.stringify(payload) });
        msg.textContent = "Saved."; msg.style.color = "var(--green)";
        invalidateSidebar();
      } catch (e) {
        msg.textContent = e.message; msg.style.color = "var(--red)";
      }
    });

    const reinitBtn = document.querySelector("[data-reinit]");
    if (reinitBtn) {
      reinitBtn.addEventListener("click", async () => {
        const payload = { ...buildBasePayload(), dslr: buildDslrPayload(true) };
        const msgEl = document.getElementById("reinit-msg");
        reinitPhase = 'saving';
        reinitErrorMsg = null;
        reinitDoneAt = null;
        reinitBtn.disabled = true;
        reinitBtn.textContent = "⏳ Re-initializing…";
        if (msgEl) msgEl.innerHTML = reinitStatusHtml();
        try {
          camera.config = await api.fetchJson(`/api/cameras/${encId}/config`, { method:"PUT", body: JSON.stringify(payload) });
          reinitPhase = 'waiting';
          if (msgEl) msgEl.innerHTML = reinitStatusHtml();
          invalidateSidebar();
        } catch (e) {
          reinitPhase = 'idle';
          reinitErrorMsg = e.message;
          reinitBtn.disabled = false;
          reinitBtn.textContent = "Re-initialize";
          if (msgEl) msgEl.innerHTML = reinitStatusHtml();
        }
      });
    }

    document.querySelector("[data-delete-camera]").addEventListener("click", () => {
      if (!confirm(`Delete camera "${cameraId}"? This cannot be undone.`)) return;
      api.fetchJson(`/api/cameras/${encId}`, { method:"DELETE" })
        .then(() => { invalidateSidebar(); window.location.hash = "#/dashboard"; })
        .catch(e => alert(e.message));
    });
  }

  await load();
  pollTimer = window.setInterval(load, 15000);
  return () => {
    if (pollTimer) window.clearInterval(pollTimer);
    framesState.mounted = false;
    removeFrameGlobalListeners();
    const marquee = document.getElementById("frames-marquee");
    if (marquee) marquee.remove();
  };
}

registerView("#/cameras/", renderCamera);
