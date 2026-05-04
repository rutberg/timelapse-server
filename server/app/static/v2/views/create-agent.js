// Create-agent wizard — 3 steps, mirrors the FastAPI server's contract
// (POST /api/agents → flash SD with provided pubkey → POST /api/agents/:id/provision).

import { api, registerView, escapeHtml, icon, renderTopbar } from "/static/v2/app.js";
import { invalidateSidebar } from "/static/v2/components/sidebar.js";

const NAME_RE = /^[a-z0-9][a-z0-9-]{0,40}$/;

async function renderWizard(root) {
  renderTopbar([{label:"Dashboard",href:"#/dashboard"},"Add camera"], "");

  const state = {
    step: 1,
    name: "",
    display_name: "",
    hostname_override: "",
    ssh_user: "pi",
    sudo_password: "",
    showPw: false,
    advancedOpen: false,
    created: null,
    provisionState: "idle", // idle | running | ok | fail
    provisionError: "",
    retryIp: "",
    nameError: "",
    copied: false,
  };

  function effectiveHostname() {
    return state.created?.expected_hostname || state.hostname_override || state.name;
  }

  function paint() {
    root.innerHTML = `
      <div style="padding:24px;max-width:880px;margin:0 auto">
        <div class="card wide" style="display:grid;grid-template-columns:240px 1fr;overflow:hidden">
          <div style="padding:18px 16px;border-right:1px solid var(--border);background:var(--panel-2)">
            ${[
              {n:1,t:"Name your camera",sub:"Generates SSH key"},
              {n:2,t:"Flash the SD card",sub:"Use Pi Imager"},
              {n:3,t:"Provision",sub:"Server SSHes in"},
            ].map((s,i)=>`
              <div class="step-item">
                <div class="step-num ${state.step>s.n?"done":state.step===s.n?"active":""}">
                  ${state.step>s.n ? icon("check",12) : s.n}
                </div>
                <div class="col" style="gap:2px">
                  <div class="lbl ink" style="font-size:9px">STEP ${s.n}</div>
                  <div style="font-size:13px">${s.t}</div>
                  <div class="mono" style="font-size:10px;color:var(--soft)">${s.sub}</div>
                </div>
              </div>
              ${i<2?'<div class="step-line"></div>':""}
            `).join("")}
          </div>
          <div style="padding:24px;min-height:480px;display:flex;flex-direction:column">
            ${state.step===1?renderStep1():""}
            ${state.step===2?renderStep2():""}
            ${state.step===3?renderStep3():""}
            ${renderFooter()}
          </div>
        </div>
      </div>`;
    wire();
  }

  function renderStep1() {
    return `
      <div class="col" style="gap:14px;flex:1">
        <div>
          <div style="font-size:16px;font-weight:500">Name your camera</div>
          <div class="small" style="margin-top:4px;line-height:1.55">
            Pick a short name. It becomes the camera's URL on this server <em>and</em> the Pi's hostname (so the server can find it as
            <span class="mono" style="color:var(--accent-2)">${escapeHtml(state.name||"<name>")}.local</span> after first boot).
          </div>
        </div>
        <label class="field">
          <span class="lbl">Camera name <span style="color:var(--red)">*</span></span>
          <input class="input" id="f-name" autofocus placeholder="tomatoes" value="${escapeHtml(state.name)}"/>
          <span class="field-help">Lowercase letters, numbers, and hyphens. 1–41 characters.</span>
        </label>
        <label class="field">
          <span class="lbl">Display name <span style="color:var(--soft)">(optional)</span></span>
          <input class="input sans" id="f-display" placeholder="Tomato camera" value="${escapeHtml(state.display_name)}"/>
        </label>
        <details ${state.advancedOpen||state.hostname_override||state.ssh_user!=="pi"?"open":""}>
          <summary class="small" style="padding:6px 0;border-top:1px solid var(--border);margin-top:4px">Advanced</summary>
          <div class="col" style="gap:10px;margin-top:10px">
            <label class="field">
              <span class="lbl">Hostname override <span style="color:var(--soft)">(default: same as camera name)</span></span>
              <input class="input" id="f-host" placeholder="${escapeHtml(state.name)}" value="${escapeHtml(state.hostname_override)}"/>
            </label>
            <label class="field">
              <span class="lbl">SSH user <span style="color:var(--soft)">(Imager default: <span class="mono">pi</span>)</span></span>
              <input class="input" id="f-user" value="${escapeHtml(state.ssh_user)}"/>
            </label>
          </div>
        </details>
        ${state.nameError?`<div class="banner bad">${icon("alert",14)}<div class="small" style="color:var(--red)">${escapeHtml(state.nameError)}</div></div>`:""}
      </div>`;
  }

  function renderStep2() {
    const host = effectiveHostname();
    const pubkey = state.created?.public_key || "";
    return `
      <div class="col" style="gap:14px;flex:1">
        <div>
          <div style="font-size:16px;font-weight:500">Flash the SD card</div>
          <div class="small" style="margin-top:4px;line-height:1.55">
            Open <a href="https://www.raspberrypi.com/software/" target="_blank" rel="noopener" style="color:var(--accent-2)">Raspberry Pi Imager</a>
            and choose <strong>Raspberry Pi OS Lite</strong>. Click the gear / "OS customisation" panel and set:
          </div>
        </div>
        <div class="card" style="background:var(--bg);padding:14px">
          <div class="col" style="gap:8px">
            ${[
              {k:"Hostname",     v:host,                 mono:true},
              {k:"Username",     v:state.ssh_user,       mono:true, note:"must match — pick any password Imager forces"},
              {k:"Wi-Fi",        v:"SSID, password, country"},
              {k:"SSH",          v:"enabled, public-key only, key below", strong:true},
              {k:"Skip",         v:"Raspberry Pi Connect", muted:true},
            ].map(c=>`
              <div class="row" style="align-items:flex-start;gap:10px;font-size:12px">
                <span class="lbl ink" style="font-size:10px;min-width:80px;margin-top:2px;${c.muted?"color:var(--soft)":""}">${c.k}</span>
                <span style="${c.muted?"color:var(--soft)":""}">
                  ${c.mono?`<span class="mono" style="color:var(--accent-2)">${escapeHtml(c.v)}</span>`
                          :c.strong?`<strong>${escapeHtml(c.v)}</strong>`:escapeHtml(c.v)}
                  ${c.note?`<span style="color:var(--soft)"> — ${escapeHtml(c.note)}</span>`:""}
                </span>
              </div>`).join("")}
          </div>
        </div>
        <div>
          <div class="between">
            <div class="lbl">Public key for this Pi</div>
            <button class="btn ghost sm" id="f-copy">${state.copied?icon("check",11)+"Copied":icon("copy",11)+"Copy key"}</button>
          </div>
          <div class="code-block" style="margin-top:6px">${escapeHtml(pubkey)}</div>
          <div class="small" style="margin-top:6px">Paste into Imager's "Set authorized_keys for SSH" field. Matching private key stays on this server only.</div>
        </div>
        <div class="banner warn">${icon("clock",14)}<div class="small" style="line-height:1.55">Flash, insert, power on. <strong>Wait 60–90 seconds</strong> for first boot before continuing.</div></div>
        <div style="border-top:1px solid var(--border);padding-top:14px">
          <label class="field">
            <span class="lbl">Pi user password <span style="color:var(--red)">*</span></span>
            <div class="row" style="gap:6px">
              <input class="input grow" id="f-sudo" type="${state.showPw?"text":"password"}" autocomplete="new-password"
                     placeholder="The password you set in Imager" value="${escapeHtml(state.sudo_password)}"/>
              <button class="btn ghost sm" id="f-show">${state.showPw?"Hide":"Show"}</button>
            </div>
            <span class="field-help">Used <strong>once over SSH</strong> to enable unattended sudo for <span class="mono">${escapeHtml(state.ssh_user)}</span>, then dropped from memory.</span>
          </label>
        </div>
      </div>`;
  }

  function renderStep3() {
    const host = effectiveHostname();
    if (state.provisionState === "running") {
      return `
        <div class="col" style="gap:14px;flex:1">
          <div class="card" style="padding:16px;background:var(--bg)">
            <div class="row" style="gap:12px">
              <div style="position:relative;width:36px;height:36px;flex-shrink:0">
                <div style="position:absolute;inset:0;border:2px solid var(--accent-2);border-radius:50%;opacity:.35;animation:pulse 1.4s infinite"></div>
                <div style="position:absolute;inset:12px;background:var(--accent-2);border-radius:50%"></div>
              </div>
              <div class="grow">
                <div style="font-size:14px;font-weight:500">Looking for ${escapeHtml(host)}.local on the LAN…</div>
                <div class="mono small" style="margin-top:4px">SSH → enable passwordless sudo → install agent</div>
              </div>
              <span class="pill warn"><span class="dot amber pulse"></span>RUNNING</span>
            </div>
          </div>
          <div class="small">This usually takes 30–60 seconds.</div>
        </div>`;
    }
    if (state.provisionState === "ok") {
      const id = state.created?.agent_id || state.name;
      return `
        <div class="col" style="gap:14px;flex:1">
          <div class="banner ok">${icon("check",16)}
            <div><strong>Success.</strong> The Pi will check in within 60 seconds.
              <div class="mono small" style="margin-top:6px;color:var(--soft)">${escapeHtml(host)}.local · sudo enabled · agent installed</div>
            </div>
          </div>
          <a class="btn accent" style="align-self:flex-start" href="#/cameras/${encodeURIComponent(id)}">Open camera${icon("arrow",12)}</a>
        </div>`;
    }
    if (state.provisionState === "fail") {
      return `
        <div class="col" style="gap:14px;flex:1">
          <div class="banner bad">${icon("x",14)}<div class="grow"><strong>Couldn't finish provisioning.</strong></div></div>
          <div class="code-block">${escapeHtml(state.provisionError)}</div>
          <details open style="background:var(--panel-2);border:1px solid var(--border);border-radius:4px;padding:10px">
            <summary class="small" style="font-weight:500">Network troubleshooting</summary>
            <ul class="small" style="margin-top:8px;padding-left:18px;line-height:1.7;color:var(--soft)">
              <li>Wait 60–90 seconds after first boot — the Pi may still be configuring Wi-Fi.</li>
              <li>Confirm the Pi has power and the activity LED has settled.</li>
              <li>For a Pi Zero W, confirm the Wi-Fi SSID is 2.4 GHz.</li>
              <li>Confirm the Wi-Fi country code was set in Imager.</li>
              <li>Confirm SSH was enabled in Imager with the public key from Step 2.</li>
            </ul>
            <div class="small" style="margin-top:8px">If <span class="mono">${escapeHtml(host)}.local</span> doesn't resolve, find the Pi's IP in your router's DHCP table.</div>
          </details>
          <label class="field">
            <span class="lbl">Pi user password <span style="color:var(--soft)">(re-enter to change)</span></span>
            <input class="input" id="f-retry-sudo" type="password" autocomplete="new-password" placeholder="${state.sudo_password?"•••••• (leave blank to reuse)":"Password"}"/>
          </label>
          <label class="field">
            <span class="lbl">Pi IP address <span style="color:var(--soft)">(optional)</span></span>
            <input class="input" id="f-retry-ip" placeholder="192.168.1.50" value="${escapeHtml(state.retryIp)}"/>
          </label>
          <div class="row" style="gap:6px">
            <button class="btn primary sm" id="f-retry">${icon("refresh",12)}Retry provision</button>
            <button class="btn ghost sm" id="f-retry-host">Retry with hostname only</button>
          </div>
        </div>`;
    }
    return "";
  }

  function renderFooter() {
    return `
      <div class="between" style="margin-top:auto;padding-top:14px;border-top:1px solid var(--border)">
        <button class="btn ghost sm" id="f-back" ${state.step===1||(state.step===3&&state.provisionState==="running")?"disabled":""}>
          ${icon("back",11)}Back
        </button>
        <div class="mono small">Step ${state.step} of 3</div>
        ${state.step===1?`<button class="btn primary sm" id="f-next1" ${!state.name?"disabled":""}>Generate provisioning key${icon("arrow",11)}</button>`:""}
        ${state.step===2?`<button class="btn primary sm" id="f-next2" ${!state.sudo_password?"disabled":""}>Provision now${icon("arrow",11)}</button>`:""}
        ${state.step===3&&state.provisionState==="ok"?`<a class="btn accent sm" href="#/dashboard">${icon("check",12)}Done</a>`:""}
        ${state.step===3&&state.provisionState==="running"?`<button class="btn primary sm" disabled>Provisioning…</button>`:""}
        ${state.step===3&&state.provisionState==="fail"?`<button class="btn ghost sm" id="f-edit">Edit settings</button>`:""}
      </div>`;
  }

  function wire() {
    const $ = (id) => document.getElementById(id);

    if (state.step === 1) {
      $("f-name")?.addEventListener("input", e => state.name = e.target.value.trim());
      $("f-display")?.addEventListener("input", e => state.display_name = e.target.value.trim());
      $("f-host")?.addEventListener("input", e => state.hostname_override = e.target.value.trim());
      $("f-user")?.addEventListener("input", e => state.ssh_user = e.target.value.trim() || "pi");
      $("f-name")?.addEventListener("input", () => $("f-next1").disabled = !state.name);
      $("f-next1")?.addEventListener("click", submitName);
    }
    if (state.step === 2) {
      $("f-sudo")?.addEventListener("input", e => { state.sudo_password = e.target.value; $("f-next2").disabled = !state.sudo_password; });
      $("f-show")?.addEventListener("click", () => { state.showPw = !state.showPw; paint(); });
      $("f-copy")?.addEventListener("click", () => {
        navigator.clipboard?.writeText(state.created?.public_key || "");
        state.copied = true; paint();
        setTimeout(() => { state.copied = false; if (state.step===2) paint(); }, 1500);
      });
      $("f-next2")?.addEventListener("click", () => provision({ ip_fallback: null }));
    }
    if (state.step === 3 && state.provisionState === "fail") {
      $("f-retry")?.addEventListener("click", () => {
        const pw = $("f-retry-sudo")?.value;
        if (pw) state.sudo_password = pw;
        state.retryIp = $("f-retry-ip")?.value.trim() || "";
        provision({ ip_fallback: state.retryIp || null });
      });
      $("f-retry-host")?.addEventListener("click", () => {
        const pw = $("f-retry-sudo")?.value;
        if (pw) state.sudo_password = pw;
        provision({ ip_fallback: null });
      });
      $("f-edit")?.addEventListener("click", () => { state.provisionState = "idle"; state.step = 2; paint(); });
    }
    $("f-back")?.addEventListener("click", () => { if (state.step>1) { state.step--; paint(); } });
  }

  async function submitName() {
    if (!NAME_RE.test(state.name)) {
      state.nameError = "1–41 chars, lowercase letters/numbers/hyphens, must start with a letter or number.";
      paint(); return;
    }
    if (state.hostname_override && !NAME_RE.test(state.hostname_override)) {
      state.nameError = "Hostname override must follow the same rules.";
      paint(); return;
    }
    state.nameError = "";
    try {
      state.created = await api.fetchJson("/api/agents", {
        method: "POST",
        body: JSON.stringify({
          agent_id: state.name,
          display_name: state.display_name || state.name,
          expected_hostname: state.hostname_override || state.name,
          ip_fallback: null,
          ssh_user: state.ssh_user,
        }),
      });
      state.step = 2; paint();
    } catch (e) { state.nameError = e.message; paint(); }
  }

  async function provision({ ip_fallback }) {
    state.step = 3; state.provisionState = "running"; paint();
    try {
      await api.fetchJson(`/api/agents/${encodeURIComponent(state.created.agent_id)}/provision`, {
        method: "POST",
        body: JSON.stringify({ ip_fallback: ip_fallback || null, sudo_password: state.sudo_password || null }),
      });
      state.sudo_password = "";
      state.provisionState = "ok";
      invalidateSidebar();
    } catch (e) {
      state.provisionState = "fail";
      state.provisionError = e.message;
    }
    paint();
  }

  paint();
}

registerView("#/agents/new", renderWizard);
