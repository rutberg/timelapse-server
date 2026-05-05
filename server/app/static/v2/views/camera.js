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

  async function load() {
    try {
      const data = await api.fetchJson("/api/cameras");
      camera = data.cameras.find(c => c.camera_id === cameraId);
      if (!camera) {
        renderTopbar([{label:"Dashboard",href:"#/dashboard"},"Unknown"], "");
        root.innerHTML = `<div class="empty"><h3>Camera not found</h3><div class="mono small">${escapeHtml(cameraId)}</div><p style="margin-top:16px"><a class="btn" href="#/dashboard">${icon("back",12)}Back to dashboard</a></p></div>`;
        return;
      }
      // While the user is on the schedule tab, don't re-paint the body — that
      // would destroy the live schedule control mid-edit and silently revert
      // unsaved selections. Just push the latest light reading into the control.
      if (tab === "schedule" && scheduleCtl) {
        scheduleCtl.setCurrentLight(camera.status?.current_light);
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
      <div style="padding:24px">
        <div class="lbl">Recent frames</div>
        <div class="small" style="margin-top:6px">A scrubbable browser of every frame this camera has captured. Wire up to <span class="mono" style="color:var(--ink)">/api/cameras/${escapeHtml(cameraId)}/frames</span> when the endpoint exists.</div>
        <div class="frame-strip" style="margin-top:14px">
          ${Array.from({length: 18}).map((_,i)=>`<div class="frame scene scene-${["day","overcast","dusk","night"][i%4]}"><div class="stamp-mini">${14-Math.floor(i/2)}:${(60-i*5+60)%60}</div></div>`).join("")}
        </div>
      </div>`;
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

  function renderDslrSection(cfg, status) {
    const dslrCfg = cfg.dslr || {};
    const dslrSt = status && status.dslr || {};
    const choices = dslrSt.choices || {};
    const expMode = dslrSt.exposure_mode || '—';
    const expBadge = expMode === 'M'
      ? `<span style="color:var(--green,#22c55e)">${escapeHtml(expMode)}</span>`
      : `<span style="color:var(--amber,#f59e0b)">${escapeHtml(expMode)} — manual mode recommended</span>`;
    const reinitPending = dslrCfg.reinit_token && dslrCfg.reinit_token !== dslrSt.last_reinit_token;

    const initOpts = (key, field, def) => DSLR_INIT_CHOICES[key].map(v =>
      `<option value="${escapeHtml(v)}" ${v === (dslrCfg[field] || def) ? 'selected' : ''}>${escapeHtml(v)}</option>`
    ).join('');

    return `
      <div class="card" style="margin-top:14px"><div class="card-b">
        <div class="lbl">DSLR Status</div>
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-top:12px;font-size:13px">
          <div><div class="lbl" style="font-size:11px">Battery</div>${escapeHtml(dslrSt.battery_level || '—')}</div>
          <div><div class="lbl" style="font-size:11px">Available shots</div>${dslrSt.available_shots != null ? Number(dslrSt.available_shots).toLocaleString() : '—'}</div>
          <div><div class="lbl" style="font-size:11px">Shutter count</div>${dslrSt.shutter_counter != null ? Number(dslrSt.shutter_counter).toLocaleString() : '—'}</div>
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
          <button class="btn" data-reinit>${reinitPending ? '⏳ Re-initializing…' : 'Re-initialize'}</button>
          <span class="small" id="reinit-msg"></span>
        </div>
      </div></div>

      <div class="card" style="margin-top:14px"><div class="card-b">
        <div class="lbl">Capture Settings</div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:12px">
          ${dslrSelect('d-shutterspeed','Shutter speed','shutterspeed',choices.shutterspeed||DSLR_DEFAULTS.shutterspeed,dslrCfg.shutterspeed)}
          ${dslrSelect('d-aperture','Aperture','aperture',choices.aperture||DSLR_DEFAULTS.aperture,dslrCfg.aperture)}
          ${dslrSelect('d-iso','ISO','iso',choices.iso||DSLR_DEFAULTS.iso,dslrCfg.iso)}
          ${dslrSelect('d-expcomp','Exposure comp.','exposurecompensation',choices.exposurecompensation||DSLR_DEFAULTS.exposurecompensation,dslrCfg.exposure_compensation)}
          ${dslrSelect('d-wb','White balance','whitebalance',choices.whitebalance||DSLR_DEFAULTS.whitebalance,dslrCfg.whitebalance)}
          ${dslrSelect('d-fmt','Image format','imageformat',choices.imageformat||DSLR_DEFAULTS.imageformat,dslrCfg.image_format)}
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
        try {
          camera.config = await api.fetchJson(`/api/cameras/${encId}/config`, { method:"PUT", body: JSON.stringify(payload) });
          reinitBtn.textContent = "⏳ Re-initializing…";
          reinitBtn.disabled = true;
          if (msgEl) { msgEl.textContent = "Sent. Camera will re-initialize on next poll."; msgEl.style.color = "var(--green)"; }
          invalidateSidebar();
        } catch (e) {
          if (msgEl) { msgEl.textContent = e.message; msgEl.style.color = "var(--red)"; }
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
  return () => { if (pollTimer) window.clearInterval(pollTimer); };
}

registerView("#/cameras/", renderCamera);
