import { api, registerView } from "/static/app.js";
import { formatBytes } from "/static/views/dashboard.js";

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function optionalNumber(id) {
  const value = document.getElementById(id).value;
  return value ? Number(value) : null;
}

async function renderCamera(root, hash) {
  const cameraId = decodeURIComponent(hash.replace("#/cameras/", "").trim());
  if (!cameraId) {
    root.innerHTML = "<article><p>Missing camera id.</p></article>";
    return;
  }

  const encodedCameraId = encodeURIComponent(cameraId);
  root.innerHTML = `
    <p><a href="#/dashboard">Back</a></p>
    <h2>Camera <code>${escapeHtml(cameraId)}</code></h2>
    <article id="status-panel"><p aria-busy="true">Loading...</p></article>
    <article id="config-panel"></article>
    <article id="image-panel"></article>
    <article id="video-panel"></article>
  `;

  let currentConfig = null;
  let configRendered = false;

  async function loadStatus() {
    try {
      const list = await api.fetchJson("/api/cameras");
      const camera = list.cameras.find((candidate) => candidate.camera_id === cameraId);
      if (!camera) {
        document.getElementById("status-panel").innerHTML = "<p>Unknown camera.</p>";
        return;
      }
      const status = camera.status || {};
      document.getElementById("status-panel").innerHTML = `
        <h3>Status</h3>
        <ul>
          <li><strong>Online:</strong> ${status.is_online ? "yes" : "no"}</li>
          <li><strong>Last seen:</strong> ${escapeHtml(status.last_seen || "-")}</li>
          <li><strong>Hostname:</strong> ${escapeHtml(status.hostname || "-")}</li>
          <li><strong>Source IP:</strong> ${escapeHtml(status.source_ip || "-")}</li>
          <li><strong>Agent version:</strong> ${escapeHtml(status.agent_version || "-")}</li>
          <li><strong>Last capture:</strong> ${escapeHtml(status.last_capture_at || "-")}</li>
          <li><strong>Last upload:</strong> ${escapeHtml(status.last_upload_at || "-")}</li>
          <li><strong>Last error:</strong> ${escapeHtml(status.last_error || "none")}</li>
          <li><strong>Image count:</strong> ${camera.image_count}</li>
          <li><strong>Pending uploads:</strong> ${status.pending_count || 0} (${formatBytes(status.pending_bytes || 0)})</li>
        </ul>
      `;
      currentConfig = camera.config;
      if (!configRendered) {
        renderConfig();
        configRendered = true;
      }
      renderImage(camera.latest_image);
    } catch (error) {
      document.getElementById("status-panel").innerHTML = `<p class="status-failed">${escapeHtml(error.message)}</p>`;
    }
  }

  function renderConfig() {
    if (!currentConfig) return;
    const config = currentConfig;
    document.getElementById("config-panel").innerHTML = `
      <h3>Capture settings</h3>
      <label><input type="checkbox" id="c-enabled" ${config.enabled ? "checked" : ""} /> Enabled</label>
      <label>Interval seconds
        <input type="number" id="c-interval" value="${config.interval_seconds}" min="30" max="86400" />
      </label>
      <div class="grid">
        <label>Width
          <input type="number" id="c-width" value="${config.image_width ?? ""}" min="320" max="10000" placeholder="full" />
        </label>
        <label>Height
          <input type="number" id="c-height" value="${config.image_height ?? ""}" min="240" max="10000" placeholder="full" />
        </label>
      </div>
      <label>JPEG quality
        <input type="number" id="c-quality" value="${config.jpeg_quality}" min="1" max="100" />
      </label>
      <label>Desired agent version
        <input id="c-version" value="${escapeHtml(config.desired_agent_version || "")}" />
      </label>
      <button type="button" id="c-save">Save</button>
      <p id="c-msg"></p>
    `;
    document.getElementById("c-save").addEventListener("click", async () => {
      const version = document.getElementById("c-version").value.trim();
      const payload = {
        enabled: document.getElementById("c-enabled").checked,
        interval_seconds: Number(document.getElementById("c-interval").value),
        image_width: optionalNumber("c-width"),
        image_height: optionalNumber("c-height"),
        jpeg_quality: Number(document.getElementById("c-quality").value),
        desired_agent_version: version || null,
      };
      try {
        currentConfig = await api.fetchJson(`/api/cameras/${encodedCameraId}/config`, {
          method: "PUT",
          body: JSON.stringify(payload),
        });
        const message = document.getElementById("c-msg");
        message.textContent = "Saved.";
        message.className = "status-online";
      } catch (error) {
        const message = document.getElementById("c-msg");
        message.textContent = error.message;
        message.className = "status-failed";
      }
    });
  }

  function renderImage(latestPath) {
    const panel = document.getElementById("image-panel");
    if (!latestPath) {
      panel.innerHTML = "<h3>Latest image</h3><p>No images yet.</p>";
      return;
    }
    panel.innerHTML = `
      <h3>Latest image</h3>
      <img class="preview" src="/api/cameras/${encodedCameraId}/latest?ts=${Date.now()}" alt="latest capture" />
    `;
  }

  document.getElementById("video-panel").innerHTML = `
    <h3>Generate video</h3>
    <div class="grid">
      <label>Start date <input type="date" id="v-start" /></label>
      <label>End date <input type="date" id="v-end" /></label>
      <label>FPS <input type="number" id="v-fps" value="24" min="1" max="60" /></label>
    </div>
    <button type="button" id="v-go">Render</button>
    <p id="v-msg"></p>
  `;
  document.getElementById("v-go").addEventListener("click", async () => {
    const payload = {
      start_date: document.getElementById("v-start").value || null,
      end_date: document.getElementById("v-end").value || null,
      fps: Number(document.getElementById("v-fps").value),
    };
    const message = document.getElementById("v-msg");
    message.textContent = "Rendering...";
    message.className = "";
    try {
      const result = await api.fetchJson(`/api/cameras/${encodedCameraId}/videos`, {
        method: "POST",
        body: JSON.stringify(payload),
      });
      const filename = result.path.split("/").pop();
      message.innerHTML = `Rendered <a href="/api/cameras/${encodedCameraId}/videos/${encodeURIComponent(filename)}">${escapeHtml(result.path)}</a>`;
      message.className = "status-online";
    } catch (error) {
      message.textContent = error.message;
      message.className = "status-failed";
    }
  });

  await loadStatus();
  const timer = window.setInterval(loadStatus, 15000);
  return () => window.clearInterval(timer);
}

registerView("#/cameras/", renderCamera);
