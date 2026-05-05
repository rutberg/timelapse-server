// Dashboard — hero featured camera + side rail of others.
// Polls /api/cameras every 10s for fresh status, latest frame thumbnails, and stats.

import {
    api,
    registerView,
    escapeHtml,
    icon,
    fmtNum,
    formatBytes,
    relativeTime,
    statusKind,
    renderTopbar,
} from "/static/v2/app.js";

function statusPill(status) {
    const kind = statusKind(status);
    if (kind === "live")
        return `<span class="pill live"><span class="dot green pulse"></span>LIVE</span>`;
    if (kind === "paused")
        return `<span class="pill off"><span class="dot grey"></span>PAUSED</span>`;
    if (kind === "failed")
        return `<span class="pill bad"><span class="dot red"></span>ERROR</span>`;
    return `<span class="pill off"><span class="dot grey"></span>OFFLINE</span>`;
}

function thumbScene(camera) {
    const id = camera.camera_id || "";
    const sum = [...id].reduce((s, ch) => s + ch.charCodeAt(0), 0);
    return ["scene-day", "scene-overcast", "scene-dusk", "scene-night"][
        sum % 4
    ];
}

function latestImageHtml(camera, opts = {}) {
    const path = camera.latest_image;
    const id = encodeURIComponent(camera.camera_id);
    if (path) {
        // Append a refresh token so the browser doesn't serve stale thumbnails
        // when polling rewrites the same URL.
        const ts = camera.status?.last_capture_at || Date.now();
        const url = `/api/cameras/${id}/latest?ts=${encodeURIComponent(ts)}`;
        return `<img src="${url}" alt="latest capture from ${escapeHtml(camera.camera_id)}" loading="${opts.lazy === false ? "eager" : "lazy"}" />`;
    }
    return `<div class="scene ${thumbScene(camera)}" style="position:absolute;inset:0"></div>
          <div class="placeholder">No frames yet</div>`;
}

function intervalLabel(seconds) {
    if (!seconds) return "—";
    if (seconds < 120) return `${seconds}s`;
    if (seconds < 3600) return `every ${Math.round(seconds / 60)} min`;
    return `every ${(seconds / 3600).toFixed(1)} h`;
}

function statCard(label, value, sub, tone) {
    const color =
        tone === "green"
            ? "var(--green)"
            : tone === "warn"
              ? "var(--accent-2)"
              : tone === "bad"
                ? "var(--red)"
                : "var(--ink)";
    return `
    <div class="card stat-card">
      <div class="lbl">${escapeHtml(label)}</div>
      <div style="display:flex;align-items:baseline;gap:8px;margin-top:6px">
        <div class="v" style="color:${color}">${value}</div>
        ${sub ? `<div class="small">${escapeHtml(sub)}</div>` : ""}
      </div>
    </div>`;
}

function renderHero(camera) {
    const display = camera.config?.display_name || camera.camera_id;
    const id = encodeURIComponent(camera.camera_id);
    const intervalText = intervalLabel(camera.config?.interval_seconds);
    return `
    <div>
      <div class="between" style="margin-bottom:10px">
        <div>
          <div class="lbl">Featured camera</div>
          <div style="font-size:22px;font-weight:600;margin-top:4px">${escapeHtml(display)}</div>
          <div class="small">${escapeHtml(camera.camera_id)} · ${intervalText} · last seen ${relativeTime(camera.status?.last_seen)}</div>
        </div>
        <div class="row">
          <button class="btn sm" data-render-cam="${id}">${icon("film", 12)}Render</button>
          <a class="btn primary sm" href="#/cameras/${id}">Open${icon("arrow", 12)}</a>
        </div>
      </div>
      <div class="cam-canvas">
        ${latestImageHtml(camera, { lazy: false })}
        <div class="cam-overlay-tl">
          ${statusPill(camera.status)}
          <span class="pill"><span class="mono" style="font-size:10px">${escapeHtml(display)}</span></span>
        </div>
        ${
            camera.status?.last_capture_at
                ? `<div class="cam-overlay-br"><span class="stamp">${escapeHtml(camera.status.last_capture_at.replace("T", " · ").slice(0, 18))}</span></div>`
                : ""
        }
      </div>
    </div>`;
}

function renderSideCard(camera) {
    const display = camera.config?.display_name || camera.camera_id;
    const id = encodeURIComponent(camera.camera_id);
    const captures = camera.image_count;
    const queued = camera.status?.pending_count || 0;
    const queuedNote = queued > 0 ? ` · ${fmtNum(queued)} queued` : "";
    return `
    <a href="#/cameras/${id}" class="card" style="padding:10px;display:flex;gap:10px;align-items:center;text-decoration:none;color:var(--ink)">
      <div class="cam-canvas" style="width:120px;flex-shrink:0;aspect-ratio:16/9;border-radius:3px">
        ${latestImageHtml(camera)}
      </div>
      <div class="grow" style="min-width:0">
        <div style="font-weight:500;font-size:13px">${escapeHtml(display)}</div>
        <div class="mono" style="font-size:10px;color:var(--soft);margin-top:3px">
          ${
              statusKind(camera.status) === "live"
                  ? '<span style="color:var(--green)">● LIVE</span>'
                  : statusKind(camera.status) === "failed"
                    ? '<span style="color:var(--red)">✕ ERROR</span>'
                    : "<span>○ " +
                      (statusKind(camera.status) === "paused"
                          ? "PAUSED"
                          : "OFFLINE") +
                      "</span>"
          }
          · ${fmtNum(captures)} frames${queuedNote}
        </div>
      </div>
      ${icon("chev", 14, "")}
    </a>`;
}

function provisioningPill(agent) {
    if (agent.status === "provisioning")
        return `<span class="pill warn"><span class="dot amber pulse"></span>PROVISIONING</span>`;
    if (agent.status === "failed")
        return `<span class="pill bad"><span class="dot red"></span>FAILED</span>`;
    return `<span class="pill off"><span class="dot grey"></span>PENDING</span>`;
}

function renderProvisioningCard(agent, onDecommission) {
    const display = agent.display_name || agent.agent_id;
    const err = agent.last_provision_error;
    return `
    <div class="card" style="padding:14px;display:flex;gap:12px;align-items:flex-start" data-agent-id="${escapeHtml(agent.agent_id)}">
      <div class="cam-canvas" style="width:120px;flex-shrink:0;aspect-ratio:16/9;border-radius:3px">
        <div class="scene ${["scene-day", "scene-overcast", "scene-dusk", "scene-night"][[...agent.agent_id].reduce((s, c) => s + c.charCodeAt(0), 0) % 4]}" style="position:absolute;inset:0"></div>
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

async function renderDashboard(root, _hash, isActive = () => true) {
    renderTopbar(
        ["Dashboard"],
        `
    <a class="btn sm" href="#/agents/new">${icon("plus", 12)}Add camera</a>
  `,
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
            const stats = data.stats || {};

            // Agents that haven't checked in yet (no camera entry) should appear as pending cards.
            const cameraIds = new Set(cameras.map((c) => c.camera_id));
            const pendingAgents = (agentsData.agents || []).filter(
                (a) => !cameraIds.has(a.agent_id),
            );

            if (cameras.length === 0 && pendingAgents.length === 0) {
                root.innerHTML = renderEmpty();
                return;
            }

            // Pick "featured" camera: prefer first online, else first failed, else first.
            const live = cameras.find((c) => statusKind(c.status) === "live");
            const failing = cameras.find(
                (c) => statusKind(c.status) === "failed",
            );
            const hero =
                cameras.length > 0 ? live || failing || cameras[0] : null;
            const rest = hero
                ? cameras.filter((c) => c.camera_id !== hero.camera_id)
                : [];

            const liveCount = cameras.filter(
                (c) => statusKind(c.status) === "live",
            ).length;
            const failingCount = cameras.filter(
                (c) => statusKind(c.status) === "failed",
            ).length;
            const totalImages = cameras.reduce(
                (s, c) => s + (c.image_count || 0),
                0,
            );
            const totalQueued = cameras.reduce(
                (s, c) => s + (c.status?.pending_count || 0),
                0,
            );

            const storageUsed = stats.storage_bytes;
            const storageCap = stats.storage_capacity_bytes;
            const storagePct =
                storageUsed && storageCap
                    ? Math.round((storageUsed / storageCap) * 100)
                    : null;

            const today = new Date().toLocaleDateString("en-GB", {
                day: "2-digit",
                month: "long",
                year: "numeric",
            });

            const totalCameraCount = cameras.length + pendingAgents.length;
            root.innerHTML = `
        <div style="padding:20px 24px;border-bottom:1px solid var(--border)">
          <div class="lbl">Overview · ${escapeHtml(today)}</div>
          <div class="stat-grid" style="margin-top:12px">
            ${statCard("Live cameras", String(liveCount), `of ${totalCameraCount}`, liveCount > 0 ? "green" : null)}
            ${statCard("Total frames", fmtNum(totalImages), totalQueued > 0 ? `${fmtNum(totalQueued)} pending` : "across all cameras", totalQueued > 0 ? "warn" : null)}
            ${statCard(
                "Storage",
                storageUsed != null ? formatBytes(storageUsed) : "—",
                storageCap
                    ? `${storagePct}% of ${formatBytes(storageCap)}`
                    : "",
                storagePct != null && storagePct > 85
                    ? "bad"
                    : storagePct != null && storagePct > 70
                      ? "warn"
                      : null,
            )}
            ${statCard(
                "Errors",
                String(failingCount),
                failingCount > 0 ? "needs attention" : "all healthy",
                failingCount > 0 ? "bad" : "green",
            )}
          </div>
        </div>

        ${
            failingCount > 0
                ? `
          <div style="padding:14px 24px 0">
            <div class="banner bad">
              ${icon("alert", 14)}
              <div class="grow">
                <strong>${failingCount} camera${failingCount === 1 ? "" : "s"} reporting errors.</strong>
                <span class="small" style="color:var(--soft)"> Open the camera list below to investigate.</span>
              </div>
            </div>
          </div>`
                : ""
        }

        ${
            pendingAgents.length > 0
                ? `
          <div style="padding:14px 24px 0">
            <div class="lbl" style="margin-bottom:10px">Provisioning · ${pendingAgents.length}</div>
            <div class="col" style="gap:8px">
              ${pendingAgents.map((a) => renderProvisioningCard(a)).join("")}
            </div>
          </div>`
                : ""
        }

        ${
            cameras.length > 0
                ? `
        <div class="hero-grid">
          ${renderHero(hero)}

          <div class="col" style="gap:12px">
            <div class="lbl">Other cameras · ${rest.length}</div>
            ${
                rest.length === 0
                    ? `<div class="small">Just the one for now.</div>`
                    : rest.map(renderSideCard).join("")
            }
          </div>
        </div>`
                : ""
        }
      `;

            // Wire the "Render" button on the hero card.
            const heroBtn = root.querySelector(`[data-render-cam]`);
            if (heroBtn) {
                heroBtn.addEventListener("click", async () => {
                    const { openRenderModal } =
                        await import("/static/v2/views/library.js");
                    openRenderModal(hero.camera_id);
                });
            }

            // Wire decommission buttons on provisioning cards.
            root.querySelectorAll("[data-decommission]").forEach((btn) => {
                btn.addEventListener("click", async () => {
                    const agentId = btn.dataset.decommission;
                    if (
                        !confirm(
                            `Decommission "${agentId}"? This will delete the agent record, SSH keys, and any captured images.`,
                        )
                    )
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
