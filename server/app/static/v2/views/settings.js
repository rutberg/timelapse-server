// Settings — server-level preferences. Placeholder for now.

import { registerView, renderTopbar } from "/static/v2/app.js";

async function renderSettings(root) {
  renderTopbar(["Settings"], "");
  root.innerHTML = `
    <div style="padding:24px;max-width:720px">
      <div class="card"><div class="card-b">
        <div class="lbl">Server</div>
        <div class="small" style="margin-top:8px;line-height:1.6">
          Server-level configuration (storage location, default capture
          interval, agent auto-update policy) lives here. The new UI keeps it
          out of every camera's detail page so per-camera settings stay focused.
        </div>
      </div></div>
      <div class="empty"><h3>Coming soon</h3><p>Wire up to <span class="mono" style="color:var(--ink)">/api/server/config</span>.</p></div>
    </div>`;
}

registerView("#/settings", renderSettings);
