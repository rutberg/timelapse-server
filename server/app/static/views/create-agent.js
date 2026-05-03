import { api, registerView } from "/static/app.js";

const NAME_RE = /^[a-z0-9][a-z0-9-]{0,40}$/;

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function renderWizard(root) {
  root.innerHTML = `
    <h2>Add agent</h2>
    <article id="wizard"></article>
  `;
  const wizard = document.getElementById("wizard");

  const state = {
    step: 1,
    form: {
      name: "",
      display_name: "",
      hostname_override: "",
      ssh_user: "pi",
    },
    created: null,
    provisionResult: null,
  };

  function renderForm() {
    wizard.innerHTML = `
      <h3>Step 1 — Name your camera</h3>
      <p>Pick a short name. It becomes the camera's URL on this server <em>and</em> the Pi's hostname (so the server can find it as <code>&lt;name&gt;.local</code> after first boot).</p>
      <label>Camera name
        <input id="f-name" required autofocus
               placeholder="tomatoes"
               value="${escapeHtml(state.form.name)}" />
        <small>Lowercase letters, numbers, and hyphens. 1–41 characters.</small>
      </label>
      <label>Display name <small>(optional)</small>
        <input id="f-display-name"
               placeholder="Tomato camera"
               value="${escapeHtml(state.form.display_name)}" />
      </label>
      <details ${state.form.hostname_override || state.form.ssh_user !== "pi" ? "open" : ""}>
        <summary>Advanced</summary>
        <label>Hostname override <small>(default: same as camera name)</small>
          <input id="f-hostname"
                 placeholder=""
                 value="${escapeHtml(state.form.hostname_override)}" />
        </label>
        <label>SSH user <small>(Imager's default first user is <code>pi</code>)</small>
          <input id="f-user" value="${escapeHtml(state.form.ssh_user || "pi")}" />
        </label>
      </details>
      <button type="button" id="f-submit">Generate provisioning key</button>
      <p id="f-error" class="status-failed"></p>
    `;
    document.getElementById("f-submit").addEventListener("click", async () => {
      const name = document.getElementById("f-name").value.trim();
      const displayName = document.getElementById("f-display-name").value.trim();
      const hostnameOverride = document.getElementById("f-hostname").value.trim();
      const sshUser = document.getElementById("f-user").value.trim() || "pi";

      state.form.name = name;
      state.form.display_name = displayName;
      state.form.hostname_override = hostnameOverride;
      state.form.ssh_user = sshUser;

      const errorEl = document.getElementById("f-error");
      if (!NAME_RE.test(name)) {
        errorEl.textContent = "Camera name must be 1–41 chars, lowercase letters/numbers/hyphens, starting alphanumeric.";
        return;
      }
      const hostname = hostnameOverride || name;
      if (!NAME_RE.test(hostname)) {
        errorEl.textContent = "Hostname override must follow the same rules as the camera name.";
        return;
      }
      errorEl.textContent = "";

      try {
        state.created = await api.fetchJson("/api/agents", {
          method: "POST",
          body: JSON.stringify({
            agent_id: name,
            display_name: displayName || name,
            expected_hostname: hostname,
            ip_fallback: null,
            ssh_user: sshUser,
          }),
        });
        state.step = 2;
        renderStep();
      } catch (error) {
        errorEl.textContent = error.message;
      }
    });
  }

  function effectiveHostname() {
    return state.created?.expected_hostname || state.form.hostname_override || state.form.name;
  }

  function renderImagerStep() {
    const hostname = effectiveHostname();
    wizard.innerHTML = `
      <h3>Step 2 — Flash the SD card</h3>
      <p>Open <a href="https://www.raspberrypi.com/software/" target="_blank" rel="noopener">Raspberry Pi Imager</a> and choose <strong>Raspberry Pi OS Lite</strong>. Click the gear / "OS customisation" panel and set:</p>
      <ul>
        <li>Hostname: <code>${escapeHtml(hostname)}</code></li>
        <li>Wi-Fi: SSID, password, country</li>
        <li>SSH: enabled, <strong>public-key only</strong>, with the key below</li>
      </ul>
      <p>Public key for this Pi:</p>
      <code class="code-block">${escapeHtml(state.created?.public_key)}</code>
      <p>Flash the SD card, insert it into the Pi, and power it on. Wait 60–90 seconds for first boot to finish.</p>
      <button type="button" id="continue">I've powered the Pi on — provision now</button>
    `;
    document.getElementById("continue").addEventListener("click", async () => {
      state.step = 3;
      renderStep();
      await provision({ ip_fallback: null });
    });
  }

  async function provision({ ip_fallback }) {
    document.getElementById("provision-status").innerHTML = `<p aria-busy="true">Looking for ${escapeHtml(effectiveHostname())}.local on the LAN…</p>`;
    try {
      const result = await api.fetchJson(`/api/agents/${encodeURIComponent(state.created.agent_id)}/provision`, {
        method: "POST",
        body: JSON.stringify({ ip_fallback: ip_fallback || null }),
      });
      state.provisionResult = { ok: true, body: result };
    } catch (error) {
      state.provisionResult = { ok: false, message: error.message };
    }
    renderProvisionResult();
  }

  function renderProvisionStep() {
    wizard.innerHTML = `
      <h3>Step 3 — Provision</h3>
      <div id="provision-status"></div>
    `;
  }

  function renderProvisionResult() {
    const status = document.getElementById("provision-status");
    if (state.provisionResult?.ok) {
      const agentId = state.created?.agent_id || state.form.name;
      status.innerHTML = `
        <p class="status-online"><strong>Success.</strong> The Pi will check in within 60 seconds.</p>
        <p><a role="button" href="#/cameras/${encodeURIComponent(agentId)}">Open camera</a></p>
      `;
      return;
    }

    const hostname = effectiveHostname();
    status.innerHTML = `
      <p class="status-failed"><strong>Couldn't reach the Pi.</strong></p>
      <p class="code-block">${escapeHtml(state.provisionResult?.message)}</p>
      <details class="troubleshooting" open>
        <summary>Troubleshooting</summary>
        <ul>
          <li>Wait 60–90 seconds after first boot — the Pi may still be configuring Wi-Fi.</li>
          <li>Confirm the Pi has power and (if it has one) the activity LED has settled.</li>
          <li>For a Pi Zero W, confirm the Wi-Fi SSID you set in Imager is 2.4 GHz.</li>
          <li>Confirm the Wi-Fi country code was set in Imager.</li>
          <li>Confirm SSH was enabled in Imager with the public key from Step 2.</li>
        </ul>
        <p>If <code>${escapeHtml(hostname)}.local</code> doesn't resolve on your network, find the Pi's IP in your router's DHCP table and enter it below to retry.</p>
      </details>
      <label>Pi IP address
        <input id="retry-ip" placeholder="192.168.1.50" />
      </label>
      <button type="button" id="retry">Retry provision</button>
      <button type="button" id="retry-hostname" class="secondary">Retry with hostname only</button>
    `;
    document.getElementById("retry").addEventListener("click", async () => {
      const ip = document.getElementById("retry-ip").value.trim();
      await provision({ ip_fallback: ip || null });
    });
    document.getElementById("retry-hostname").addEventListener("click", async () => {
      await provision({ ip_fallback: null });
    });
  }

  function renderStep() {
    if (state.step === 1) renderForm();
    else if (state.step === 2) renderImagerStep();
    else if (state.step === 3) renderProvisionStep();
  }

  renderStep();
}

registerView("#/agents/new", renderWizard);
