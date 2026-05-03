# Phase D: Web UI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Prerequisites:** Phases A, B, and C complete and merged. Phase D consumes API contracts from all three.

**Goal:** A LAN-only browser UI for creating agents, monitoring camera status, changing capture settings, viewing latest images, and triggering video generation.

**Architecture:** A vanilla single-page app served by FastAPI. No build step. **Alpine.js 3.x** drives interactivity, **pico.css** supplies semantic-element styling, both vendored under `server/app/static/vendor/` so the LXC works offline. Hash-based routing (`#/dashboard`, `#/agents/new`, `#/cameras/<id>`) so each view is an independent JS module. Each view fetches via the existing JSON API.

**Tech Stack:** Plain HTML + Alpine.js + pico.css. No new Python deps.

---

## Wave structure

```
Wave 1 (sequential):  D1 → D2
Wave 2 (parallel):    [D3 Dashboard] [D4 Create-Agent] [D5 Camera detail]
Wave 3 (sequential):  D6 → D7
```

D3, D4, D5 each own their own JS module file under `server/app/static/views/`. They share no state and can be implemented by separate subagents at the same time.

---

## File structure

**New files:**
- `server/app/static/index.html` — shell with `<main>`, `<nav>`, hash router placeholder.
- `server/app/static/app.js` — hash router + view registry.
- `server/app/static/vendor/alpine.min.js` — pinned 3.13.x.
- `server/app/static/vendor/pico.min.css` — pinned 2.x.
- `server/app/static/views/dashboard.js`
- `server/app/static/views/create-agent.js`
- `server/app/static/views/camera.js`
- `server/app/static/styles.css` — small layer of project styles on top of pico.
- `tests/server/test_ui_routes.py`

**Modified files:**
- `server/app/main.py` — `app.mount("/static", StaticFiles(...))`, serve `index.html` at `/`.

---

### Task D1: Vendor assets + static mount (wave 1)

**Files:**
- Create: `server/app/static/vendor/alpine.min.js`
- Create: `server/app/static/vendor/pico.min.css`
- Create: `server/app/static/.gitkeep` (if needed)

- [ ] **Step 1: Create vendor directory**

```bash
mkdir -p server/app/static/vendor
```

- [ ] **Step 2: Download Alpine.js 3.13.10**

```bash
curl -fsSL -o server/app/static/vendor/alpine.min.js \
  https://cdn.jsdelivr.net/npm/alpinejs@3.13.10/dist/cdn.min.js
```
Expected: file size > 30KB.

- [ ] **Step 3: Download pico.css 2.0.6 (classless build)**

```bash
curl -fsSL -o server/app/static/vendor/pico.min.css \
  https://cdn.jsdelivr.net/npm/@picocss/pico@2.0.6/css/pico.classless.min.css
```
Expected: file size > 30KB.

- [ ] **Step 4: Verify files exist**

```bash
ls -la server/app/static/vendor/
```
Expected: both files present.

- [ ] **Step 5: Commit**

```bash
git add server/app/static/vendor/
git commit -m "chore: vendor Alpine.js 3.13.10 and pico.css 2.0.6"
```

---

### Task D2: Static mount + shell + router (wave 1)

**Files:**
- Modify: `server/app/main.py`
- Create: `server/app/static/index.html`
- Create: `server/app/static/app.js`
- Create: `server/app/static/styles.css`
- Create: `tests/server/test_ui_routes.py`

- [ ] **Step 1: Write the failing test**

`tests/server/test_ui_routes.py`:
```python
def test_root_serves_html_shell(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "<title>Timelapse" in body
    assert "/static/app.js" in body
    assert "x-data" in body  # Alpine bootstrapping


def test_static_assets_served(client):
    response = client.get("/static/vendor/alpine.min.js")
    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]


def test_unknown_route_falls_through_to_shell(client):
    # Hash routing means /agents/new is handled client-side; the shell
    # must serve at /agents/new too so refreshing a deep link works.
    response = client.get("/agents/new")
    assert response.status_code == 200
    assert "<title>Timelapse" in response.text
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/server/test_ui_routes.py -v`
Expected: 404s.

- [ ] **Step 3: Add the static mount + catch-all to `server/app/main.py`**

At the top with other imports:
```python
from fastapi.staticfiles import StaticFiles
```

After the `lan_only_middleware`:
```python
STATIC_DIR = Path(__file__).resolve().parent / "static"

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")


@app.get("/{path:path}", include_in_schema=False)
def spa_fallback(path: str) -> FileResponse:
    if path.startswith("api/") or path.startswith("static/"):
        raise HTTPException(status_code=404)
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")
```

- [ ] **Step 4: Create `server/app/static/index.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Timelapse</title>
  <link rel="stylesheet" href="/static/vendor/pico.min.css" />
  <link rel="stylesheet" href="/static/styles.css" />
  <script defer src="/static/vendor/alpine.min.js"></script>
  <script type="module" src="/static/app.js"></script>
</head>
<body x-data="{ route: window.location.hash || '#/dashboard' }"
      @hashchange.window="route = window.location.hash || '#/dashboard'">
  <header class="container">
    <nav>
      <ul>
        <li><strong>Timelapse</strong></li>
      </ul>
      <ul>
        <li><a href="#/dashboard">Cameras</a></li>
        <li><a href="#/agents/new">Add agent</a></li>
      </ul>
    </nav>
  </header>
  <main id="app-root" class="container">
    <p aria-busy="true">Loading…</p>
  </main>
</body>
</html>
```

- [ ] **Step 5: Create `server/app/static/styles.css`**

```css
:root { --pico-spacing: 0.9rem; }
.tile-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 1rem;
}
.status-online { color: var(--pico-color-jade-500); }
.status-offline { color: var(--pico-color-grey-500); }
.status-failed { color: var(--pico-color-red-500); }
.troubleshooting {
  background: var(--pico-card-background-color);
  padding: 1rem;
  border-radius: var(--pico-border-radius);
}
img.preview { width: 100%; border-radius: var(--pico-border-radius); }
.code-block {
  display: block;
  white-space: pre-wrap;
  word-break: break-all;
  background: var(--pico-code-background-color);
  padding: 0.75rem;
  border-radius: var(--pico-border-radius);
}
```

- [ ] **Step 6: Create `server/app/static/app.js`**

```javascript
const views = new Map();

export function registerView(prefix, render) {
  views.set(prefix, render);
}

async function fetchJson(url, options = {}) {
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

async function render() {
  const root = document.getElementById("app-root");
  const hash = window.location.hash || "#/dashboard";
  for (const [prefix, view] of views.entries()) {
    if (hash.startsWith(prefix)) {
      root.innerHTML = "";
      try {
        await view(root, hash);
      } catch (error) {
        root.innerHTML = `<article><h2>Error</h2><p>${error.message}</p></article>`;
      }
      return;
    }
  }
  root.innerHTML = "<article><h2>Not found</h2></article>";
}

window.addEventListener("hashchange", render);

await Promise.all([
  import("/static/views/dashboard.js"),
  import("/static/views/create-agent.js"),
  import("/static/views/camera.js"),
]);

if (!window.location.hash) {
  window.location.hash = "#/dashboard";
}
render();
```

- [ ] **Step 7: Create empty placeholders for view modules so the dynamic imports succeed**

```bash
for view in dashboard create-agent camera; do
  cat > "server/app/static/views/${view}.js" <<'JS'
import { registerView } from "/static/app.js";
JS
done
```

(D3, D4, D5 will fill these in.)

- [ ] **Step 8: Run tests**

Run: `pytest tests/server -v`
Expected: all pass including the 3 new UI route tests.

- [ ] **Step 9: Smoke-test in a browser**

Run:
```bash
TIMELAPSE_DATA_DIR=/tmp/tl-smoke .venv-dev/bin/python -m uvicorn app.main:app --app-dir server --port 8080
```
Open `http://127.0.0.1:8080`. Expected: navigation bar visible, "Loading…" then "Not found" or empty (views are stubs). No console errors except expected 404s on view-stub functions.

- [ ] **Step 10: Commit**

```bash
git add server/app/main.py server/app/static/ tests/server/test_ui_routes.py
git commit -m "feat(ui): static mount, SPA shell, hash router scaffold"
```

---

### Task D3: Dashboard view (wave 2, dashboard branch)

**Files:**
- Modify: `server/app/static/views/dashboard.js`

The dashboard lists every camera with status and a link to its detail page. Auto-refreshes every 10s.

- [ ] **Step 1: Implement `server/app/static/views/dashboard.js`**

```javascript
import { registerView, api } from "/static/app.js";

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
  if (!iso) return "—";
  const seconds = (Date.now() - new Date(iso).getTime()) / 1000;
  if (seconds < 60) return `${Math.round(seconds)}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  return `${Math.round(seconds / 3600)}h ago`;
}

async function renderDashboard(root) {
  root.innerHTML = `
    <h2>Cameras</h2>
    <p><a role="button" href="#/agents/new">Add agent</a></p>
    <div class="tile-grid" id="camera-tiles"><p aria-busy="true">Loading…</p></div>
  `;

  let timer;
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
      tiles.innerHTML = data.cameras.map((c) => `
        <article>
          <header>
            <strong>${c.camera_id}</strong>
            <span class="${statusClass(c.status)}">${statusLabel(c.status)}</span>
          </header>
          <p>
            Last seen: ${relativeTime(c.status?.last_seen)}<br/>
            Last capture: ${relativeTime(c.status?.last_capture_at)}<br/>
            Images: ${c.image_count}
          </p>
          ${c.status?.last_error ? `<p class="status-failed">Error: ${c.status.last_error}</p>` : ""}
          <footer><a href="#/cameras/${c.camera_id}" role="button">Open</a></footer>
        </article>
      `).join("");
    } catch (error) {
      const tiles = document.getElementById("camera-tiles");
      if (tiles) tiles.innerHTML = `<p class="status-failed">Failed to load: ${error.message}</p>`;
    }
  }

  await load();
  timer = window.setInterval(load, 10_000);

  return () => window.clearInterval(timer);
}

registerView("#/dashboard", renderDashboard);
```

- [ ] **Step 2: Manual smoke check**

Refresh the dev server (Phase A's heartbeat sample is enough — POST to `/api/cameras/test/checkin`). Visit `http://127.0.0.1:8080#/dashboard`. Expected: a tile for `test` with `online` status.

- [ ] **Step 3: Commit**

```bash
git add server/app/static/views/dashboard.js
git commit -m "feat(ui): dashboard view with auto-refreshing camera tiles"
```

---

### Task D4: Create-Agent wizard (wave 2, wizard branch)

**Files:**
- Modify: `server/app/static/views/create-agent.js`

Three-step wizard:
1. Form: agent_id, display name, hostname, optional IP fallback, SSH user, OS picker (cosmetic — drives instructions text).
2. Show generated public key + Imager instructions.
3. Click "Provision now" → POST provision → show success or troubleshooting.

- [ ] **Step 1: Implement `server/app/static/views/create-agent.js`**

```javascript
import { registerView, api } from "/static/app.js";

const OS_INSTRUCTIONS = {
  macos: "Open Raspberry Pi Imager (brew install raspberry-pi-imager). Choose Raspberry Pi OS Lite.",
  windows: "Open Raspberry Pi Imager (download from raspberrypi.com). Choose Raspberry Pi OS Lite.",
  linux: "Open Raspberry Pi Imager (sudo apt install rpi-imager). Choose Raspberry Pi OS Lite.",
};

async function renderWizard(root) {
  root.innerHTML = `
    <h2>Add agent</h2>
    <article id="wizard"></article>
  `;
  const wizard = document.getElementById("wizard");

  const state = {
    step: 1,
    form: {
      agent_id: "",
      display_name: "",
      expected_hostname: "",
      ip_fallback: "",
      ssh_user: "pi",
      os: "macos",
    },
    created: null,
    provisionResult: null,
  };

  function form() {
    wizard.innerHTML = `
      <h3>Step 1 — Describe the agent</h3>
      <label>Agent id (URL-safe, e.g. tomatoes-zero-w)
        <input id="f-agent-id" required />
      </label>
      <label>Display name
        <input id="f-display-name" />
      </label>
      <label>Expected hostname (will be set in Imager, e.g. timelapse-tomatoes)
        <input id="f-hostname" required />
      </label>
      <label>IP fallback (optional, used if hostname doesn't resolve)
        <input id="f-ip" placeholder="192.168.1.50" />
      </label>
      <label>SSH user (Imager default is "pi")
        <input id="f-user" value="pi" required />
      </label>
      <label>Workstation OS (for Imager instructions)
        <select id="f-os">
          <option value="macos">macOS</option>
          <option value="windows">Windows</option>
          <option value="linux">Linux</option>
        </select>
      </label>
      <button id="f-submit">Generate provisioning key</button>
      <p id="f-error" class="status-failed"></p>
    `;
    document.getElementById("f-submit").addEventListener("click", async () => {
      state.form.agent_id = document.getElementById("f-agent-id").value.trim();
      state.form.display_name = document.getElementById("f-display-name").value.trim() || state.form.agent_id;
      state.form.expected_hostname = document.getElementById("f-hostname").value.trim();
      state.form.ip_fallback = document.getElementById("f-ip").value.trim();
      state.form.ssh_user = document.getElementById("f-user").value.trim();
      state.form.os = document.getElementById("f-os").value;
      try {
        state.created = await api.fetchJson("/api/agents", {
          method: "POST",
          body: JSON.stringify({
            agent_id: state.form.agent_id,
            display_name: state.form.display_name,
            expected_hostname: state.form.expected_hostname,
            ip_fallback: state.form.ip_fallback || null,
            ssh_user: state.form.ssh_user,
          }),
        });
        state.step = 2;
        flashStep();
      } catch (error) {
        document.getElementById("f-error").textContent = error.message;
      }
    });
  }

  function imagerStep() {
    wizard.innerHTML = `
      <h3>Step 2 — Flash the SD card</h3>
      <p>${OS_INSTRUCTIONS[state.form.os]}</p>
      <p>In the OS-customisation panel, set:</p>
      <ul>
        <li>Hostname: <code>${state.form.expected_hostname}</code></li>
        <li>SSH: enabled, <strong>using public-key only</strong>, with the key below</li>
        <li>Wi-Fi SSID, password, and country</li>
      </ul>
      <p>Public key for this agent:</p>
      <code class="code-block">${state.created.public_key}</code>
      <p>Flash the SD card, insert it into the Pi, and power the Pi on. Wait 60–90 seconds for first boot.</p>
      <button id="continue">I've powered the Pi on — Provision now</button>
    `;
    document.getElementById("continue").addEventListener("click", async () => {
      state.step = 3;
      flashStep();
      await provision();
    });
  }

  async function provision() {
    document.getElementById("provision-status").innerHTML = `<p aria-busy="true">Provisioning over SSH…</p>`;
    try {
      const result = await api.fetchJson(`/api/agents/${state.created.agent_id}/provision`, {
        method: "POST",
        body: JSON.stringify({
          ip_fallback: state.form.ip_fallback || null,
        }),
      });
      state.provisionResult = { ok: true, body: result };
    } catch (error) {
      state.provisionResult = { ok: false, message: error.message };
    }
    renderProvision();
  }

  function provisionStep() {
    wizard.innerHTML = `
      <h3>Step 3 — Provision</h3>
      <div id="provision-status"></div>
    `;
  }

  function renderProvision() {
    const status = document.getElementById("provision-status");
    if (state.provisionResult?.ok) {
      status.innerHTML = `
        <p class="status-online"><strong>Success.</strong> The Pi will check in within 60 seconds.</p>
        <p><a role="button" href="#/cameras/${state.created.agent_id}">Open camera</a></p>
      `;
    } else {
      status.innerHTML = `
        <p class="status-failed"><strong>Provisioning failed.</strong></p>
        <p class="code-block">${state.provisionResult?.message ?? ""}</p>
        <details class="troubleshooting" open>
          <summary>Troubleshooting</summary>
          <ul>
            <li>Confirm the Pi has power and the LED has stopped flashing.</li>
            <li>Wait 60–90 seconds after first boot.</li>
            <li>For a Pi Zero W, confirm the Wi-Fi SSID is 2.4GHz.</li>
            <li>Confirm the Wi-Fi country code was set in Imager.</li>
            <li>Confirm SSH was enabled with the <em>public key</em> shown in Step 2.</li>
            <li>Check your router's DHCP table for <code>${state.form.expected_hostname}</code> and enter that IP below.</li>
          </ul>
        </details>
        <label>IP fallback
          <input id="retry-ip" value="${state.form.ip_fallback}" />
        </label>
        <button id="retry">Retry provision</button>
      `;
      document.getElementById("retry").addEventListener("click", async () => {
        state.form.ip_fallback = document.getElementById("retry-ip").value.trim();
        await provision();
      });
    }
  }

  function flashStep() {
    if (state.step === 1) form();
    else if (state.step === 2) imagerStep();
    else if (state.step === 3) provisionStep();
  }

  flashStep();
}

registerView("#/agents/new", renderWizard);
```

- [ ] **Step 2: Manual smoke check**

Visit `http://127.0.0.1:8080#/agents/new`. Fill the form with `agent_id=test-pi`, `expected_hostname=timelapse-test`. Submit. Expected: Step 2 shows a public key starting with `ssh-ed25519`.

- [ ] **Step 3: Commit**

```bash
git add server/app/static/views/create-agent.js
git commit -m "feat(ui): three-step Create-Agent wizard with provisioning + retry"
```

---

### Task D5: Camera detail view (wave 2, camera branch)

**Files:**
- Modify: `server/app/static/views/camera.js`

Shows config form, status panel, latest image preview, and a "Generate video" button.

- [ ] **Step 1: Implement `server/app/static/views/camera.js`**

```javascript
import { registerView, api } from "/static/app.js";

async function renderCamera(root, hash) {
  const cameraId = hash.replace("#/cameras/", "").trim();
  if (!cameraId) {
    root.innerHTML = "<article><p>Missing camera id.</p></article>";
    return;
  }

  root.innerHTML = `
    <p><a href="#/dashboard">← Back</a></p>
    <h2>Camera <code>${cameraId}</code></h2>
    <article id="status-panel"><p aria-busy="true">Loading…</p></article>
    <article id="config-panel"></article>
    <article id="image-panel"></article>
    <article id="video-panel"></article>
  `;

  let timer;
  let currentConfig = null;

  async function loadStatus() {
    try {
      const list = await api.fetchJson("/api/cameras");
      const camera = list.cameras.find((c) => c.camera_id === cameraId);
      if (!camera) {
        document.getElementById("status-panel").innerHTML = `<p>Unknown camera.</p>`;
        return;
      }
      const status = camera.status || {};
      document.getElementById("status-panel").innerHTML = `
        <h3>Status</h3>
        <ul>
          <li><strong>Online:</strong> ${status.is_online ? "yes" : "no"}</li>
          <li><strong>Last seen:</strong> ${status.last_seen ?? "—"}</li>
          <li><strong>Hostname:</strong> ${status.hostname ?? "—"}</li>
          <li><strong>Source IP:</strong> ${status.source_ip ?? "—"}</li>
          <li><strong>Agent version:</strong> ${status.agent_version ?? "—"}</li>
          <li><strong>Last capture:</strong> ${status.last_capture_at ?? "—"}</li>
          <li><strong>Last upload:</strong> ${status.last_upload_at ?? "—"}</li>
          <li><strong>Last error:</strong> ${status.last_error ?? "none"}</li>
          <li><strong>Image count:</strong> ${camera.image_count}</li>
        </ul>
      `;
      currentConfig = camera.config;
      renderConfig();
      renderImage(camera.latest_image);
    } catch (error) {
      document.getElementById("status-panel").innerHTML = `<p class="status-failed">${error.message}</p>`;
    }
  }

  function renderConfig() {
    if (!currentConfig) return;
    const c = currentConfig;
    document.getElementById("config-panel").innerHTML = `
      <h3>Capture settings</h3>
      <label><input type="checkbox" id="c-enabled" ${c.enabled ? "checked" : ""} /> Enabled</label>
      <label>Interval (seconds)
        <input type="number" id="c-interval" value="${c.interval_seconds}" min="30" max="86400" />
      </label>
      <div class="grid">
        <label>Width
          <input type="number" id="c-width" value="${c.image_width ?? ""}" placeholder="full" />
        </label>
        <label>Height
          <input type="number" id="c-height" value="${c.image_height ?? ""}" placeholder="full" />
        </label>
      </div>
      <label>JPEG quality
        <input type="number" id="c-quality" value="${c.jpeg_quality}" min="1" max="100" />
      </label>
      <label>Desired agent version (blank = no upgrade)
        <input id="c-version" value="${c.desired_agent_version ?? ""}" />
      </label>
      <button id="c-save">Save</button>
      <p id="c-msg"></p>
    `;
    document.getElementById("c-save").addEventListener("click", async () => {
      const widthVal = document.getElementById("c-width").value;
      const heightVal = document.getElementById("c-height").value;
      const versionVal = document.getElementById("c-version").value.trim();
      const payload = {
        enabled: document.getElementById("c-enabled").checked,
        interval_seconds: Number(document.getElementById("c-interval").value),
        image_width: widthVal ? Number(widthVal) : null,
        image_height: heightVal ? Number(heightVal) : null,
        jpeg_quality: Number(document.getElementById("c-quality").value),
        desired_agent_version: versionVal || null,
      };
      try {
        await api.fetchJson(`/api/cameras/${cameraId}/config`, {
          method: "PUT",
          body: JSON.stringify(payload),
        });
        document.getElementById("c-msg").textContent = "Saved.";
        document.getElementById("c-msg").className = "status-online";
      } catch (error) {
        document.getElementById("c-msg").textContent = error.message;
        document.getElementById("c-msg").className = "status-failed";
      }
    });
  }

  function renderImage(latestPath) {
    const panel = document.getElementById("image-panel");
    if (!latestPath) {
      panel.innerHTML = `<h3>Latest image</h3><p>No images yet.</p>`;
      return;
    }
    panel.innerHTML = `
      <h3>Latest image</h3>
      <img class="preview" src="/api/cameras/${cameraId}/latest?ts=${Date.now()}" alt="latest capture" />
    `;
  }

  document.getElementById("video-panel").innerHTML = `
    <h3>Generate video</h3>
    <div class="grid">
      <label>Start date <input type="date" id="v-start" /></label>
      <label>End date <input type="date" id="v-end" /></label>
      <label>FPS <input type="number" id="v-fps" value="24" min="1" max="60" /></label>
    </div>
    <button id="v-go">Render</button>
    <p id="v-msg"></p>
  `;
  document.getElementById("v-go").addEventListener("click", async () => {
    const payload = {
      start_date: document.getElementById("v-start").value || null,
      end_date: document.getElementById("v-end").value || null,
      fps: Number(document.getElementById("v-fps").value),
    };
    document.getElementById("v-msg").textContent = "Rendering…";
    try {
      const result = await api.fetchJson(`/api/cameras/${cameraId}/videos`, {
        method: "POST",
        body: JSON.stringify(payload),
      });
      document.getElementById("v-msg").innerHTML =
        `Rendered <a href="/api/cameras/${cameraId}/videos/${result.path.split("/").pop()}">${result.path}</a>`;
    } catch (error) {
      document.getElementById("v-msg").textContent = error.message;
    }
  });

  await loadStatus();
  timer = window.setInterval(loadStatus, 15_000);
}

registerView("#/cameras/", renderCamera);
```

- [ ] **Step 2: Manual smoke check**

POST a heartbeat for `test`, then visit `http://127.0.0.1:8080#/cameras/test`. Expected: status panel populated, config form editable, video form rendered.

- [ ] **Step 3: Commit**

```bash
git add server/app/static/views/camera.js
git commit -m "feat(ui): camera detail view with config, latest image, video render"
```

---

### Task D6: Polish + navigation states (wave 3)

**Files:**
- Modify: `server/app/static/index.html`
- Modify: `server/app/static/styles.css`
- Modify: `server/app/static/app.js`
- Test: extend `tests/server/test_ui_routes.py`

Add: active-link highlight, visible loading/error banners, prevent flash of unrouted state.

- [ ] **Step 1: Add active link logic to `app.js`**

After `await Promise.all(...)` in `app.js`, before the final `render()`:
```javascript
function highlightActive() {
  const hash = window.location.hash || "#/dashboard";
  document.querySelectorAll("nav a").forEach((a) => {
    if (hash.startsWith(a.getAttribute("href"))) {
      a.setAttribute("aria-current", "page");
    } else {
      a.removeAttribute("aria-current");
    }
  });
}
window.addEventListener("hashchange", highlightActive);
highlightActive();
```

- [ ] **Step 2: Add `[aria-current=page]` style to `styles.css`**

```css
nav a[aria-current="page"] { font-weight: 700; text-decoration: underline; }
```

- [ ] **Step 3: Add a footer with server version to `index.html`**

After `</main>`:
```html
  <footer class="container">
    <small>Timelapse server — LAN only</small>
  </footer>
```

- [ ] **Step 4: Manual smoke check**

Reload. Click between Cameras and Add agent links. Expected: active link is bold + underlined.

- [ ] **Step 5: Add a test that all three view modules are reachable**

Append to `tests/server/test_ui_routes.py`:
```python
def test_view_modules_served(client):
    for module in ("dashboard.js", "create-agent.js", "camera.js"):
        response = client.get(f"/static/views/{module}")
        assert response.status_code == 200, module
        assert "registerView" in response.text


def test_app_js_served(client):
    response = client.get("/static/app.js")
    assert response.status_code == 200
    assert "hashchange" in response.text
```

- [ ] **Step 6: Run tests**

Run: `pytest tests/server -v`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add server/app/static/ tests/server/test_ui_routes.py
git commit -m "feat(ui): nav active state, footer, view-module smoke tests"
```

---

### Task D7: Documentation

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add UI section to README**

Insert under `## Server Setup`:
```markdown
### Web UI

After install, browse to `http://<SERVER_IP>:<PORT>/` from any LAN host. The UI provides:

- **Cameras** — auto-refreshing status tiles for every camera that has checked in.
- **Add agent** — three-step wizard (form → flash with Imager → SSH provision) backed by `/api/agents`.
- **Camera detail** — capture settings, latest image preview, video render button, and `desired_agent_version` for rolling out updates.

The UI is served from `/static/` and uses Alpine.js + pico.css. Both are vendored, so the LXC works offline.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: web UI overview"
```

---

## Self-review checklist

- [ ] No external CDN fetches at runtime (all assets under `static/vendor/`).
- [ ] LAN-only middleware applies to `/`, `/static/*`, and SPA fallback paths.
- [ ] SPA fallback does not shadow `/api/*` or `/static/*` routes.
- [ ] All API calls go through `api.fetchJson` so error rendering is consistent.
- [ ] `agent_id` and `camera_id` shown in URLs match what the backend validators allow (`safe_identifier`).
- [ ] Camera detail view never sends `desired_agent_version: ""` — empty string is mapped to `null` in the payload.

---

## Out of scope (deferred to later phases)

- **Capture-now button.** Requires server config flag + agent ack handshake. Not implemented in any of A/B/C/D.
- **SSH-disable-after-provisioning toggle.** PRD lists as opt-in; UI surface deferred.
- **Diagnostics report viewer.**
- **Daily/weekly scheduled video jobs.**
- **Local admin authentication.** Server still relies on LAN-only middleware; UI assumes the LAN itself is the authentication boundary.
