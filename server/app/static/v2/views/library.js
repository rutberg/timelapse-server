// Library — render history & "new render" modal.

import { api, registerView, escapeHtml, icon, fmtNum, renderTopbar, openModal } from "/static/v2/app.js";

async function renderLibrary(root) {
  renderTopbar(["Library"], `<button class="btn primary sm" id="lib-new">${icon("film",12)}New render</button>`);
  root.innerHTML = `
    <div style="padding:24px">
      <div class="empty">
        <h3>Render history</h3>
        <p>This view will list every MP4/GIF rendered from any camera once the
        server exposes a <span class="mono">/api/videos</span> endpoint. For now,
        click <strong>New render</strong> above to start one.</p>
      </div>
    </div>`;
  document.getElementById("lib-new").addEventListener("click", async () => {
    const data = await api.fetchJson("/api/cameras");
    if (!data.cameras?.length) { alert("Add a camera first."); return; }
    openRenderModal(data.cameras[0].camera_id);
  });
}

export function openRenderModal(cameraId) {
  const encId = encodeURIComponent(cameraId);
  let fps = 24, format = "mp4", busy = false, message = "";

  function html() {
    return `
      <div class="modal-h">
        <div>
          <div class="lbl">Render</div>
          <div style="font-size:16px;font-weight:500;margin-top:2px">${escapeHtml(cameraId)}</div>
        </div>
        <button class="btn ghost sm" data-modal-close>${icon("x",14)}</button>
      </div>
      <div class="modal-b">
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px">
          <label class="field"><span class="lbl">Start date</span><input class="input" id="r-start" type="date"/></label>
          <label class="field"><span class="lbl">End date</span><input class="input" id="r-end" type="date"/></label>
        </div>
        <div style="margin-top:16px">
          <div class="lbl">Frame rate</div>
          <div class="seg" style="margin-top:6px" id="r-fps-seg">
            ${[12,24,30,60].map(f=>`<button data-fps="${f}" class="${f===fps?"active":""}">${f}</button>`).join("")}
          </div>
        </div>
        <div style="margin-top:16px">
          <div class="lbl">Format</div>
          <div class="seg" style="margin-top:6px" id="r-fmt-seg">
            ${["mp4","gif","webm"].map(f=>`<button data-fmt="${f}" class="${f===format?"active":""}">${f.toUpperCase()}</button>`).join("")}
          </div>
        </div>
        ${message?`<div class="small" style="margin-top:14px;color:${message.startsWith("Error")?"var(--red)":"var(--green)"}">${escapeHtml(message)}</div>`:""}
      </div>
      <div class="modal-foot">
        <button class="btn ghost" data-modal-close>Cancel</button>
        <button class="btn accent" id="r-go" ${busy?"disabled":""}>${icon("film",12)}${busy?"Rendering…":"Render now"}</button>
      </div>`;
  }

  const modal = openModal(html());
  function rerender() { modal.root.querySelector(".modal").innerHTML = html(); wire(); }
  function wire() {
    modal.root.querySelectorAll("[data-fps]").forEach(b => b.addEventListener("click", () => { fps = Number(b.dataset.fps); rerender(); }));
    modal.root.querySelectorAll("[data-fmt]").forEach(b => b.addEventListener("click", () => { format = b.dataset.fmt; rerender(); }));
    modal.root.querySelector("#r-go")?.addEventListener("click", async () => {
      busy = true; message = ""; rerender();
      try {
        const r = await api.fetchJson(`/api/cameras/${encId}/videos`, {
          method: "POST",
          body: JSON.stringify({
            start_date: document.getElementById("r-start").value || null,
            end_date:   document.getElementById("r-end").value || null,
            fps, format,
          }),
        });
        const filename = (r.path||"").split("/").pop();
        message = `Rendered ${r.path}`;
        busy = false; rerender();
      } catch (e) { busy = false; message = "Error: " + e.message; rerender(); }
    });
  }
  wire();
}

registerView("#/library", renderLibrary);
