// Dashboard — adaptive wall whose tile sizes scale with the *active* fleet.
//
// Total wall area is fixed; as N grows tiles shrink, as N shrinks the featured
// tile expands. Layout reflows through distinct "modes" so it always looks
// intentional, never just shrunken:
//
//   1        →  solo: one giant featured fills the wall
//   2        →  duo: two equal heroes
//   3        →  trio: featured + 2 stacked
//   4–6      →  hero + 3-up tail
//   7–10     →  hero + 2 vertical companions + dense tail
//   11–16    →  smaller hero + dense tail (no large featured if you're scanning)
//   17+      →  pure dense uniform grid
//
// Polls /api/cameras every 10s. Falls back gracefully when the fleet is empty
// or has only pending (not-yet-checked-in) agents.

import {
    api,
    registerView,
    escapeHtml,
    icon,
    fmtNum,
    relativeTime,
    statusKind,
    renderTopbar,
} from "/static/v2/app.js";

// ---- Status helpers ---------------------------------------------------------

function statusPill(status, { solid = false } = {}) {
    const kind = statusKind(status);
    const cls = kind === "live" ? "live" : kind === "failed" ? "bad" : kind === "paused" ? "off" : "off";
    const dot = kind === "live" ? "green" : kind === "failed" ? "red" : "grey";
    const label = kind === "live" ? "LIVE" : kind === "failed" ? "ERROR" : kind === "paused" ? "PAUSED" : "OFFLINE";
    return `<span class="pill ${solid ? "solid " : ""}${cls}"><span class="dot ${dot}${kind === "live" ? " pulse" : ""}"></span>${label}</span>`;
}

const SCENES = ["scene-day", "scene-overcast", "scene-dusk", "scene-night", "scene-dawn", "scene-fog", "scene-water"];
function thumbScene(camera) {
    const id = camera.camera_id || "";
    const sum = [...id].reduce((s, ch) => s + ch.charCodeAt(0), 0);
    return SCENES[sum % SCENES.length];
}

function imageHtml(camera) {
    const path = camera.latest_image;
    if (path) {
        const id = encodeURIComponent(camera.camera_id);
        const ts = camera.status?.last_capture_at || Date.now();
        const url = `/api/cameras/${id}/latest?ts=${encodeURIComponent(ts)}`;
        return `<img src="${url}" alt="latest capture from ${escapeHtml(camera.camera_id)}" loading="lazy" />`;
    }
    return `<div class="scene ${thumbScene(camera)}" style="position:absolute;inset:0"></div>
            <div class="placeholder">No frames yet</div>`;
}

function intervalLabel(seconds) {
    if (!seconds) return "—";
    if (seconds < 120) return `${seconds}s`;
    if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
    return `${(seconds / 3600).toFixed(1)}h`;
}

function lastSeenLabel(camera) {
    const t = camera.status?.last_capture_at || camera.status?.last_seen;
    if (!t) return "never";
    return relativeTime(t);
}

function placeLabel(camera) {
    return camera.config?.location_label || camera.hostname || "—";
}

// Deterministic 24-bar sparkline. Until the API exposes per-hour capture
// history this stands in for the design's "24h activity" rail; keying off
// camera_id keeps each camera visually distinct without animating noise.
function sparkBars(seed, count = 24) {
    const out = [];
    let x = (seed || 1) * 1234.567;
    for (let i = 0; i < count; i++) {
        x = (x * 9301 + 49297) % 233280;
        out.push(0.3 + (x / 233280) * 0.7);
    }
    return out;
}

function sparkSeed(camera) {
    const id = camera.camera_id || "";
    return [...id].reduce((s, ch) => s + ch.charCodeAt(0), 0) || 1;
}

function sparkHtml(camera) {
    const bars = sparkBars(sparkSeed(camera))
        .map((v) => `<span style="height:${(v * 100).toFixed(1)}%"></span>`)
        .join("");
    return `<div class="bar-row">${bars}</div>`;
}

const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function daysLabel(days) {
    if (!days || days.length === 0 || days.length === 7) return null;
    const sorted = [...days].sort((a, b) => a - b);
    if (sorted.join(",") === "1,2,3,4,5") return "Mon – Fri";
    if (sorted.join(",") === "6,7") return "Sat – Sun";
    const isRange = sorted.every((d, i) => i === 0 || d === sorted[i - 1] + 1);
    if (isRange) return `${DAY_NAMES[sorted[0] - 1]} – ${DAY_NAMES[sorted[sorted.length - 1] - 1]}`;
    return sorted.map(d => DAY_NAMES[d - 1]).join(", ");
}

function hoursLabel(hours) {
    if (!hours || hours.length === 0) return null;
    const sorted = [...hours].sort((a, b) => a - b);
    const pad = n => String(n).padStart(2, "0");
    return `${pad(sorted[0])}:00 – ${pad(sorted[sorted.length - 1])}:00`;
}

function scheduleLabel(camera) {
    const cfg = camera.config;
    const interval = intervalLabel(cfg?.interval_seconds);
    const mode = cfg?.schedule_mode;
    const days = daysLabel(cfg?.schedule_days);
    let main;
    if (mode === "scene") {
        main = `every ${interval} @ Ȳ ≥ ${cfg.light_threshold ?? "?"}`;
    } else if (mode === "hours" && cfg?.capture_hours?.length) {
        main = `${hoursLabel(cfg.capture_hours)} · every ${interval}`;
    } else if (mode === "daylight") {
        main = `every ${interval} (daylight)`;
    } else {
        main = `every ${interval}`;
    }
    return days ? `${main} · ${days}` : main;
}

function captureRateLabel(camera) {
    const i = camera.config?.interval_seconds;
    if (!i) return "—";
    if (i <= 60) return `${(60 / i).toFixed(i < 30 ? 1 : 0)}/min`;
    return `${(3600 / i).toFixed(i < 600 ? 0 : 1)}/h`;
}

// ---- Tile -------------------------------------------------------------------
//
// size: xl | lg | md | sm | xs — controls aspect, padding, font weight
// orientation: "horizontal" | "vertical"
// showStats: include the at-a-glance stat strip (hero) or stat rows (vertical)
function tileHtml(camera, opts = {}) {
    const {
        size = "md",
        orientation = "horizontal",
        showStats = false,
        showSpark = false,
        showStamp = true,
        showRender = false,
        featured = false,
    } = opts;

    const compact = size === "sm" || size === "xs";
    const isVertical = orientation === "vertical";
    const display = camera.config?.display_name || camera.camera_id;
    const id = encodeURIComponent(camera.camera_id);

    const queued = camera.status?.pending_count || 0;
    const lastCap = camera.status?.last_capture_at;
    const stampText = lastCap
        ? (statusKind(camera.status) === "live"
            ? `● LIVE · ${relativeTime(lastCap)}`
            : `${relativeTime(lastCap)}`)
        : "no frames";

    const note = camera.status?.last_error;
    const noteKind = statusKind(camera.status) === "failed" ? "bad" : note ? "warn" : null;

    const subText = compact && !isVertical
        ? `${fmtNum(camera.image_count || 0)} · ${intervalLabel(camera.config?.interval_seconds)}`
        : `${escapeHtml(placeLabel(camera))} · ${scheduleLabel(camera)}`;

    const showMeta = !(size === "xs" && opts.showMeta === false);

    return `
    <a href="#/cameras/${id}" class="tile size-${size} ${compact ? "compact" : ""} ${isVertical ? "vertical" : ""}">
      <div class="tile-image">
        ${imageHtml(camera)}
        <div class="tl">${statusPill(camera.status, { solid: true })}</div>
        ${queued > 0 ? `<div class="tr"><span class="pill solid"><span class="num" style="font-size:9px">↑ ${fmtNum(queued)}</span></span></div>` : ""}
        ${showStamp ? `<div class="bl"><span class="stamp">${escapeHtml(stampText)}</span></div>` : ""}
        ${showRender ? `<div class="br"><button class="btn sm render-overlay" data-render-cam="${id}">${icon("film", 11)}Render</button></div>` : ""}
      </div>
      ${showMeta ? `
        <div class="tile-meta">
          <div class="between" style="align-items:flex-start">
            <div class="grow" style="min-width:0">
              <div style="display:flex;align-items:center;gap:4px">
                <div class="tile-name">${escapeHtml(display)}</div>
                ${size !== "xs" ? `<button class="tile-star${featured ? " on" : ""}" data-feature-cam="${id}" data-featured="${featured ? "1" : ""}">${icon("star", compact ? 10 : 12)}</button>` : ""}
              </div>
              <div class="tile-sub">${subText}</div>
              ${note && (!compact || isVertical) ? `<div class="tile-note ${noteKind || "warn"}">${escapeHtml(note)}</div>` : ""}
            </div>
          </div>
          ${showStats && isVertical ? renderStatsVertical(camera) : ""}
          ${showStats && !isVertical ? renderStatsHorizontal(camera) : ""}
          ${showSpark ? renderSpark(camera, { featured, isVertical }) : ""}
        </div>
      ` : ""}
    </a>`;
}

function renderSpark(camera, { featured = false, isVertical = false } = {}) {
    const caption = featured ? "24h capture rate" : "24h activity";
    const right = featured
        ? `<span class="num small">peak ${captureRateLabel(camera)}</span>`
        : "";
    const wrapStyle = isVertical ? "margin-top:auto" : "";
    return `
      <div class="tile-spark" style="${wrapStyle}">
        <div class="between"><span class="lbl">${caption}</span>${right}</div>
        ${sparkHtml(camera)}
      </div>`;
}

function renderStatsHorizontal(camera) {
    const queued = camera.status?.pending_count || 0;
    return `
      <div class="tile-stats-h">
        <div class="tile-stat-h"><div class="lbl">Frames</div><div class="v">${fmtNum(camera.image_count || 0)}</div></div>
        <div class="tile-stat-h"><div class="lbl">Interval</div><div class="v">${intervalLabel(camera.config?.interval_seconds)}</div></div>
        <div class="tile-stat-h"><div class="lbl">Queued</div><div class="v ${queued > 0 ? "warn" : ""}">${fmtNum(queued)}</div></div>
        <div class="tile-stat-h"><div class="lbl">Last</div><div class="v">${escapeHtml(lastSeenLabel(camera))}</div></div>
      </div>`;
}

function renderStatsVertical(camera) {
    const queued = camera.status?.pending_count || 0;
    return `
      <div class="tile-stats-v">
        <div class="tile-stat-v"><span class="lbl">Frames</span><span class="v">${fmtNum(camera.image_count || 0)}</span></div>
        <div class="tile-stat-v"><span class="lbl">Interval</span><span class="v">${intervalLabel(camera.config?.interval_seconds)}</span></div>
        <div class="tile-stat-v"><span class="lbl">Queued</span><span class="v ${queued > 0 ? "warn" : ""}">${fmtNum(queued)}</span></div>
        <div class="tile-stat-v"><span class="lbl">Last</span><span class="v">${escapeHtml(lastSeenLabel(camera))}</span></div>
      </div>`;
}

// ---- Adaptive layout --------------------------------------------------------
//
// Each mode returns { label, html(sortedFleet) }. The fleet is pre-sorted
// most-attention-needed first, so fleet[0] is always the hero.

function modeFor(n) {
    if (n <= 1) return MODE_SOLO;
    if (n === 2) return MODE_DUO;
    if (n === 3) return MODE_TRIO;
    if (n <= 6) return MODE_HERO_3;
    if (n <= 10) return MODE_HERO_2_TAIL;
    if (n <= 16) return MODE_HERO_DENSE;
    return MODE_DENSE_ONLY;
}

const MODE_SOLO = {
    label: "Solo · one big window",
    html: (fleet) => `
      <div class="wall" style="padding:22px">
        <div style="max-width:1100px;margin:0 auto;width:100%">
          ${tileHtml(fleet[0], { size: "xl", showStats: true, showSpark: true, showRender: true, featured: !!fleet[0].config?.featured_at })}
        </div>
      </div>`,
};

const MODE_DUO = {
    label: "Duo · twin heroes",
    html: (fleet) => `
      <div class="wall" style="grid-template-columns:1fr 1fr;gap:16px">
        ${fleet.map((c) => tileHtml(c, { size: "lg", showStats: true, showSpark: true, featured: !!c.config?.featured_at })).join("")}
      </div>`,
};

const MODE_TRIO = {
    label: "Trio · featured + two companions",
    html: (fleet) => `
      <div class="wall" style="grid-template-columns:2fr 1fr;gap:14px">
        ${tileHtml(fleet[0], { size: "xl", showStats: true, showSpark: true, showRender: true, featured: !!fleet[0].config?.featured_at })}
        <div class="col" style="gap:14px">
          ${tileHtml(fleet[1], { size: "md", showStats: true, showSpark: true, featured: !!fleet[1].config?.featured_at })}
          ${tileHtml(fleet[2], { size: "md", showStats: true, showSpark: true, featured: !!fleet[2].config?.featured_at })}
        </div>
      </div>`,
};

const MODE_HERO_3 = {
    label: "Featured + 3-up",
    html: (fleet) => {
        const [hero, ...rest] = fleet;
        const cols = Math.max(rest.length, 1);
        return `
          <div class="wall" style="grid-template-rows:auto auto;gap:16px">
            ${tileHtml(hero, { size: "xl", showStats: true, showSpark: true, showRender: true, featured: !!hero.config?.featured_at })}
            <div style="display:grid;grid-template-columns:repeat(${cols},1fr);gap:12px">
              ${rest.map(c => tileHtml(c, { size: "md", featured: !!c.config?.featured_at })).join("")}
            </div>
          </div>`;
    },
};

const MODE_HERO_2_TAIL = {
    label: "Featured + 2 companions + tail",
    html: (fleet) => {
        const hero = fleet[0];
        const top2 = fleet.slice(1, 3);
        const tail = fleet.slice(3);
        const tailCols = Math.min(Math.max(tail.length, 1), 5);
        return `
          <div class="wall" style="display:block">
            <div style="display:grid;grid-template-columns:2fr 1fr 1fr;gap:14px;align-items:stretch">
              ${tileHtml(hero, { size: "xl", showStats: true, showSpark: true, showRender: true, featured: !!hero.config?.featured_at })}
              ${top2.map(c => tileHtml(c, { size: "lg", orientation: "vertical", showStats: true, showSpark: true, featured: !!c.config?.featured_at })).join("")}
            </div>
            ${tail.length > 0 ? `
              <div class="wall-section-label" style="margin-top:22px;margin-bottom:10px">All cameras · ${tail.length}</div>
              <div style="display:grid;grid-template-columns:repeat(${tailCols},1fr);gap:10px">
                ${tail.map(c => tileHtml(c, { size: "sm" })).join("")}
              </div>` : ""}
          </div>`;
    },
};

const MODE_HERO_DENSE = {
    label: "Smaller hero + dense tail",
    html: (fleet) => {
        const hero = fleet[0];
        const top3 = fleet.slice(1, 4);
        const tail = fleet.slice(4);
        return `
          <div class="wall" style="display:block">
            <div style="display:grid;grid-template-columns:2fr 1fr 1fr 1fr;gap:12px">
              ${tileHtml(hero, { size: "lg", showStats: true, showSpark: true, featured: !!hero.config?.featured_at })}
              ${top3.map(c => tileHtml(c, { size: "sm", featured: !!c.config?.featured_at })).join("")}
            </div>
            ${tail.length > 0 ? `
              <div class="wall-section-label" style="margin-top:18px;margin-bottom:10px">Tail · ${tail.length}</div>
              <div style="display:grid;grid-template-columns:repeat(6,1fr);gap:8px">
                ${tail.map(c => tileHtml(c, { size: "xs", showStamp: false })).join("")}
              </div>` : ""}
          </div>`;
    },
};

const MODE_DENSE_ONLY = {
    label: "Dense uniform grid",
    html: (fleet) => {
        const cols = fleet.length > 24 ? 8 : fleet.length > 16 ? 7 : 6;
        return `
          <div class="wall" style="display:block">
            <div class="wall-section-label" style="margin-bottom:10px">All cameras · ${fleet.length}</div>
            <div style="display:grid;grid-template-columns:repeat(${cols},1fr);gap:8px">
              ${fleet.map(c => tileHtml(c, { size: "xs", showStamp: false })).join("")}
            </div>
          </div>`;
    },
};

// ---- Provisioning, empty state ---------------------------------------------

function provisioningPill(agent) {
    if (agent.status === "provisioning")
        return `<span class="pill warn"><span class="dot amber pulse"></span>PROVISIONING</span>`;
    if (agent.status === "failed")
        return `<span class="pill bad"><span class="dot red"></span>FAILED</span>`;
    return `<span class="pill off"><span class="dot grey"></span>PENDING</span>`;
}

function renderProvisioningCard(agent) {
    const display = agent.display_name || agent.agent_id;
    const err = agent.last_provision_error;
    const sceneIdx = [...agent.agent_id].reduce((s, c) => s + c.charCodeAt(0), 0) % SCENES.length;
    return `
    <div class="card" style="padding:14px;display:flex;gap:12px;align-items:flex-start" data-agent-id="${escapeHtml(agent.agent_id)}">
      <div class="cam-canvas" style="width:120px;flex-shrink:0;aspect-ratio:16/9;border-radius:3px">
        <div class="scene ${SCENES[sceneIdx]}" style="position:absolute;inset:0"></div>
        <div class="placeholder">Waiting for first checkin</div>
      </div>
      <div class="grow" style="min-width:0">
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
          <span style="font-weight:500;font-size:13px">${escapeHtml(display)}</span>
          ${provisioningPill(agent)}
        </div>
        <div class="mono small" style="color:var(--soft);margin-top:3px">${escapeHtml(agent.agent_id)} · ${escapeHtml(agent.expected_hostname)}.local</div>
        ${err ? `<div class="mono small" style="color:var(--red);margin-top:4px;white-space:pre-wrap;word-break:break-all">${escapeHtml(err)}</div>` : ""}
      </div>
      <div class="row" style="flex-shrink:0;gap:6px">
        ${agent.status === "failed" ? `<a class="btn ghost sm" href="#/agents/new">${icon("refresh", 11)}Re-provision</a>` : ""}
        <button class="btn danger sm" data-decommission="${escapeHtml(agent.agent_id)}">Decommission</button>
      </div>
    </div>`;
}

function renderEmpty() {
    return `
    <div class="empty">
      <h3>No cameras yet</h3>
      <p>Add a Raspberry Pi agent to start capturing.</p>
      <p style="margin-top:16px"><a class="btn primary" href="#/agents/new">${icon("plus", 14)}Add your first camera</a></p>
    </div>`;
}

// ---- Sort: featured first (newest featured = slot 1), then status/frames ---
function sortFleet(cameras) {
    const rank = { failed: 0, warn: 1, live: 2, paused: 3, offline: 4, unknown: 5 };
    return [...cameras].sort((a, b) => {
        const aFeat = a.config?.featured_at || null;
        const bFeat = b.config?.featured_at || null;
        if (aFeat && !bFeat) return -1;
        if (!aFeat && bFeat) return 1;
        if (aFeat && bFeat) return bFeat.localeCompare(aFeat); // newer = lower index
        const r = (rank[statusKind(a.status)] ?? 9) - (rank[statusKind(b.status)] ?? 9);
        if (r !== 0) return r;
        return (b.image_count || 0) - (a.image_count || 0);
    });
}

// ---- Main view --------------------------------------------------------------

async function renderDashboard(root, _hash, isActive = () => true) {
    renderTopbar(
        ["Dashboard"],
        `<a class="btn sm primary" href="#/agents/new">${icon("plus", 12)}Add camera</a>`,
    );

    root.innerHTML = `<div class="loading mono small">Loading…</div>`;
    let timer = null;

    async function load() {
        try {
            const [data, agentsData] = await Promise.all([
                api.fetchJson("/api/cameras"),
                api.fetchJson("/api/agents").catch(() => ({ agents: [] })),
            ]);
            if (!isActive()) return;

            const cameras = data.cameras || [];
            const cameraIds = new Set(cameras.map((c) => c.camera_id));
            const pendingAgents = (agentsData.agents || []).filter(
                (a) => !cameraIds.has(a.agent_id),
            );

            if (cameras.length === 0 && pendingAgents.length === 0) {
                root.innerHTML = renderEmpty();
                return;
            }

            const sorted = sortFleet(cameras);
            const liveCount = cameras.filter((c) => statusKind(c.status) === "live").length;
            const failingCount = cameras.filter((c) => statusKind(c.status) === "failed").length;
            const warnCount = cameras.filter((c) => {
                const k = statusKind(c.status);
                return k !== "live" && k !== "failed" && k !== "unknown";
            }).length;

            const now = new Date();
            const todayDate = now.toLocaleDateString("en-GB", {
                day: "2-digit",
                month: "long",
                year: "numeric",
            });
            const todayTime = now.toLocaleTimeString("en-GB", {
                hour: "2-digit",
                minute: "2-digit",
            });

            const mode = sorted.length > 0 ? modeFor(sorted.length) : null;

            root.innerHTML = `
              <div class="fleet-summary">
                <div class="lbl">Fleet · ${escapeHtml(todayDate)} · ${escapeHtml(todayTime)}</div>
                <div class="head">
                  ${liveCount} of ${cameras.length} cameras live
                  ${failingCount > 0 ? `<span class="warn" style="color:var(--red)">· ${failingCount} error${failingCount === 1 ? "" : "s"}</span>` : ""}
                  ${warnCount > 0 ? `<span class="warn">· ${warnCount} idle</span>` : ""}
                </div>
              </div>

              ${failingCount > 0 ? `
                <div style="padding:14px 24px 0">
                  <div class="banner bad">
                    ${icon("alert", 14)}
                    <div class="grow">
                      <strong>${failingCount} camera${failingCount === 1 ? "" : "s"} reporting errors.</strong>
                      <span class="small" style="color:var(--soft)"> The hero tile shows the most urgent first.</span>
                    </div>
                  </div>
                </div>` : ""}

              ${pendingAgents.length > 0 ? `
                <div style="padding:14px 24px 0">
                  <div class="lbl" style="margin-bottom:10px">Provisioning · ${pendingAgents.length}</div>
                  <div class="col" style="gap:8px">
                    ${pendingAgents.map(renderProvisioningCard).join("")}
                  </div>
                </div>` : ""}

              ${mode ? mode.html(sorted) : ""}
            `;

            // Wire render-overlay buttons (hero render shortcut). The link's
            // own click bubbles up first; we cancel that when the inner button
            // is the actual target so we don't navigate away.
            root.querySelectorAll("[data-render-cam]").forEach((btn) => {
                btn.addEventListener("click", async (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    const camId = decodeURIComponent(btn.dataset.renderCam);
                    const { openRenderModal } = await import("/static/v2/views/library.js");
                    openRenderModal(camId);
                });
            });

            // Featured star toggles on dashboard tiles.
            root.querySelectorAll("[data-feature-cam]").forEach((btn) => {
                btn.addEventListener("click", async (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    const camId = decodeURIComponent(btn.dataset.featureCam);
                    const featured = !btn.dataset.featured;
                    try {
                        await api.fetchJson(
                            `/api/cameras/${encodeURIComponent(camId)}/feature`,
                            { method: "POST", body: JSON.stringify({ featured }) },
                        );
                        await load();
                    } catch (_) {}
                });
            });

            // Decommission for stuck/failed pending agents.
            root.querySelectorAll("[data-decommission]").forEach((btn) => {
                btn.addEventListener("click", async (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    const agentId = btn.dataset.decommission;
                    if (!confirm(`Decommission "${agentId}"? This will delete the agent record, SSH keys, and any captured images.`))
                        return;
                    try {
                        await api.fetchJson(
                            `/api/cameras/${encodeURIComponent(agentId)}`,
                            { method: "DELETE" },
                        );
                    } catch (_) {}
                    await load();
                });
            });
        } catch (error) {
            if (!isActive()) return;
            root.innerHTML = `
              <div class="empty">
                <h3>Failed to load cameras</h3>
                <div class="mono small">${escapeHtml(error.message)}</div>
              </div>`;
        }
    }

    await load();
    if (!isActive()) return () => {};
    timer = window.setInterval(load, 10000);
    return () => {
        if (timer) window.clearInterval(timer);
    };
}

registerView("#/dashboard", renderDashboard);
