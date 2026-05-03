import { api, registerView } from "/static/app.js";

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function statusClass(status) {
  if (status?.is_online) return "status-online";
  if (status?.last_error) return "status-failed";
  return "status-offline";
}

function statusLabel(status) {
  if (status?.is_online) return "online";
  if (status?.last_seen) return "offline";
  return "never seen";
}

function relativeTime(iso) {
  if (!iso) return "-";
  const seconds = (Date.now() - new Date(iso).getTime()) / 1000;
  if (Number.isNaN(seconds)) return "-";
  if (seconds < 60) return `${Math.max(0, Math.round(seconds))}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  return `${Math.round(seconds / 3600)}h ago`;
}

async function renderDashboard(root) {
  root.innerHTML = `
    <h2>Cameras</h2>
    <p><a role="button" href="#/agents/new">Add agent</a></p>
    <div class="tile-grid" id="camera-tiles"><p aria-busy="true">Loading...</p></div>
  `;

  async function load() {
    try {
      const data = await api.fetchJson("/api/cameras");
      const tiles = document.getElementById("camera-tiles");
      if (!tiles) return;
      if (!data.cameras.length) {
        tiles.innerHTML = `
          <article>
            <p>No cameras yet.</p>
            <p><a href="#/agents/new">Add your first agent</a>.</p>
          </article>`;
        return;
      }
      tiles.innerHTML = data.cameras.map((camera) => `
        <article>
          <header>
            <strong>${escapeHtml(camera.camera_id)}</strong>
            <span class="${statusClass(camera.status)}">${statusLabel(camera.status)}</span>
          </header>
          <p>
            Last seen: ${relativeTime(camera.status?.last_seen)}<br />
            Last capture: ${relativeTime(camera.status?.last_capture_at)}<br />
            Images: ${camera.image_count}
          </p>
          ${camera.status?.last_error ? `<p class="status-failed">Error: ${escapeHtml(camera.status.last_error)}</p>` : ""}
          <footer><a href="#/cameras/${encodeURIComponent(camera.camera_id)}" role="button">Open</a></footer>
        </article>
      `).join("");
    } catch (error) {
      const tiles = document.getElementById("camera-tiles");
      if (tiles) tiles.innerHTML = `<p class="status-failed">Failed to load: ${escapeHtml(error.message)}</p>`;
    }
  }

  await load();
  const timer = window.setInterval(load, 10000);
  return () => window.clearInterval(timer);
}

registerView("#/dashboard", renderDashboard);
