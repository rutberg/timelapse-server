import { api, registerView } from "/static/app.js";

const OS_INSTRUCTIONS = {
  macos: "Open Raspberry Pi Imager. Choose Raspberry Pi OS Lite.",
  windows: "Open Raspberry Pi Imager from raspberrypi.com. Choose Raspberry Pi OS Lite.",
  linux: "Open Raspberry Pi Imager from your package manager. Choose Raspberry Pi OS Lite.",
};

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

  function renderForm() {
    wizard.innerHTML = `
      <h3>Step 1 - Describe the agent</h3>
      <label>Agent id
        <input id="f-agent-id" required placeholder="tomatoes-zero-w" />
      </label>
      <label>Display name
        <input id="f-display-name" />
      </label>
      <label>Expected hostname
        <input id="f-hostname" required placeholder="timelapse-tomatoes" />
      </label>
      <label>IP fallback
        <input id="f-ip" placeholder="192.168.1.50" />
      </label>
      <label>SSH user
        <input id="f-user" value="pi" required />
      </label>
      <label>Workstation OS
        <select id="f-os">
          <option value="macos">macOS</option>
          <option value="windows">Windows</option>
          <option value="linux">Linux</option>
        </select>
      </label>
      <button type="button" id="f-submit">Generate provisioning key</button>
      <p id="f-error" class="status-failed"></p>
    `;
    document.getElementById("f-submit").addEventListener("click", async () => {
      state.form.agent_id = document.getElementById("f-agent-id").value.trim();
      state.form.display_name = document.getElementById("f-display-name").value.trim() || state.form.agent_id;
      state.form.expected_hostname = document.getElementById("f-hostname").value.trim();
      state.form.ip_fallback = document.getElementById("f-ip").value.trim();
      state.form.ssh_user = document.getElementById("f-user").value.trim();
      state.form.os = document.getElementById("f-os").value;
      if (!state.form.agent_id || !state.form.expected_hostname || !state.form.ssh_user) {
        document.getElementById("f-error").textContent = "Agent id, hostname, and SSH user are required.";
        return;
      }
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
        renderStep();
      } catch (error) {
        document.getElementById("f-error").textContent = error.message;
      }
    });
  }

  function renderImagerStep() {
    wizard.innerHTML = `
      <h3>Step 2 - Flash the SD card</h3>
      <p>${OS_INSTRUCTIONS[state.form.os]}</p>
      <p>In the OS customisation panel, set:</p>
      <ul>
        <li>Hostname: <code>${escapeHtml(state.form.expected_hostname)}</code></li>
        <li>SSH: enabled, using public-key only, with the key below</li>
        <li>Wi-Fi SSID, password, and country</li>
      </ul>
      <p>Public key for this agent:</p>
      <code class="code-block">${escapeHtml(state.created?.public_key)}</code>
      <p>Flash the SD card, insert it into the Pi, and power the Pi on. Wait 60-90 seconds for first boot.</p>
      <button type="button" id="continue">Provision now</button>
    `;
    document.getElementById("continue").addEventListener("click", async () => {
      state.step = 3;
      renderStep();
      await provision();
    });
  }

  async function provision() {
    document.getElementById("provision-status").innerHTML = `<p aria-busy="true">Provisioning over SSH...</p>`;
    try {
      const result = await api.fetchJson(`/api/agents/${encodeURIComponent(state.created.agent_id)}/provision`, {
        method: "POST",
        body: JSON.stringify({
          ip_fallback: state.form.ip_fallback || null,
        }),
      });
      state.provisionResult = { ok: true, body: result };
    } catch (error) {
      state.provisionResult = { ok: false, message: error.message };
    }
    renderProvisionResult();
  }

  function renderProvisionStep() {
    wizard.innerHTML = `
      <h3>Step 3 - Provision</h3>
      <div id="provision-status"></div>
    `;
  }

  function renderProvisionResult() {
    const status = document.getElementById("provision-status");
    if (state.provisionResult?.ok) {
      const agentId = state.created?.agent_id || state.form.agent_id;
      status.innerHTML = `
        <p class="status-online"><strong>Success.</strong> The Pi will check in within 60 seconds.</p>
        <p><a role="button" href="#/cameras/${encodeURIComponent(agentId)}">Open camera</a></p>
      `;
      return;
    }

    status.innerHTML = `
      <p class="status-failed"><strong>Provisioning failed.</strong></p>
      <p class="code-block">${escapeHtml(state.provisionResult?.message)}</p>
      <details class="troubleshooting" open>
        <summary>Troubleshooting</summary>
        <ul>
          <li>Confirm the Pi has power and the LED has stopped flashing.</li>
          <li>Wait 60-90 seconds after first boot.</li>
          <li>For a Pi Zero W, confirm the Wi-Fi SSID is 2.4GHz.</li>
          <li>Confirm the Wi-Fi country code was set in Imager.</li>
          <li>Confirm SSH was enabled with the public key shown in Step 2.</li>
          <li>Check your router DHCP table for <code>${escapeHtml(state.form.expected_hostname)}</code> and enter that IP below.</li>
        </ul>
      </details>
      <label>IP fallback
        <input id="retry-ip" value="${escapeHtml(state.form.ip_fallback)}" />
      </label>
      <button type="button" id="retry">Retry provision</button>
    `;
    document.getElementById("retry").addEventListener("click", async () => {
      state.form.ip_fallback = document.getElementById("retry-ip").value.trim();
      await provision();
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
