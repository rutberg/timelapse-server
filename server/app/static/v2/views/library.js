// Library — render history & "new render" modal.

import {
    api,
    registerView,
    escapeHtml,
    icon,
    fmtNum,
    renderTopbar,
    openModal,
} from "/static/v2/app.js";

async function renderLibrary(root) {
    renderTopbar(
        ["Library"],
        `<button class="btn primary sm" id="lib-new">${icon("film", 12)}New render</button>`,
    );
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
        if (!data.cameras?.length) {
            alert("Add a camera first.");
            return;
        }
        openRenderModal(data.cameras[0].camera_id);
    });
}

export function openRenderModal(cameraId, initialRangeHours = 24) {
    const encId = encodeURIComponent(cameraId);
    let fps = 24,
        format = "mp4",
        busy = false,
        progress = 0,
        message = "";

    let startDate,
        startHour,
        endDate,
        endHour,
        modal = null,
        activeRange = null;

    function setRange(hours) {
        const end = new Date();
        let start;
        if (hours === "all") {
            start = new Date(0);
            startDate = "2020-01-01";
            startHour = 0;
        } else {
            start = new Date(end.getTime() - hours * 60 * 60 * 1000);
            startDate = start.toISOString().split("T")[0];
            startHour = start.getHours();
        }
        endDate = end.toISOString().split("T")[0];
        endHour = end.getHours();
        activeRange = hours;

        // Only rerender if the modal is already open
        if (modal) rerender();
    }

    setRange(initialRangeHours);

    function html() {
        const hours = Array.from({ length: 24 }, (_, i) => i);
        return `
      <div class="modal-h">
        <div>
          <div class="lbl">Render</div>
          <div style="font-size:16px;font-weight:500;margin-top:2px">${escapeHtml(cameraId)}</div>
        </div>
        <button class="btn ghost sm" data-modal-close>${icon("x", 14)}</button>
      </div>
      <div class="modal-b">
        <div class="lbl">Quick intervals</div>
        <div class="seg" style="margin-top:6px;margin-bottom:16px">
          <button data-range="12" class="${activeRange === 12 ? "active" : ""}">12 hours</button>
          <button data-range="24" class="${activeRange === 24 ? "active" : ""}">24 hours</button>
          <button data-range="168" class="${activeRange === 168 ? "active" : ""}">7 days</button>
          <button data-range="all" class="${activeRange === "all" ? "active" : ""}">All time</button>
        </div>


        <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px">
          <div class="field">
            <span class="lbl">Start</span>
            <div style="display:flex;gap:4px">
              <input class="input" id="r-start-d" type="date" value="${startDate}" style="flex:2"/>
              <select class="input" id="r-start-h" style="flex:1">
                ${hours.map((h) => `<option value="${h}" ${h === startHour ? "selected" : ""}>${h.toString().padStart(2, "0")}:00</option>`).join("")}
              </select>
            </div>
          </div>
          <div class="field">
            <span class="lbl">End</span>
            <div style="display:flex;gap:4px">
              <input class="input" id="r-end-d" type="date" value="${endDate}" style="flex:2"/>
              <select class="input" id="r-end-h" style="flex:1">
                ${hours.map((h) => `<option value="${h}" ${h === endHour ? "selected" : ""}>${h.toString().padStart(2, "0")}:00</option>`).join("")}
              </select>
            </div>
          </div>
        </div>

        <div style="margin-top:16px">
          <div class="lbl">Frame rate</div>
          <div class="seg" style="margin-top:6px" id="r-fps-seg">
            ${[12, 24, 30, 60].map((f) => `<button data-fps="${f}" class="${f === fps ? "active" : ""}">${f}</button>`).join("")}
          </div>
        </div>
        <div style="margin-top:16px">
          <div class="lbl">Format</div>
          <div class="seg" style="margin-top:6px" id="r-fmt-seg">
            ${["mp4", "gif"].map((f) => `<button data-fmt="${f}" class="${f === format ? "active" : ""}">${f.toUpperCase()}</button>`).join("")}
          </div>
        </div>
        ${busy ? `
        <div style="margin-top:14px">
          <progress value="${progress}" max="100" style="width:100%"></progress>
          <div class="mono small" style="text-align:center;margin-top:4px">${progress}%</div>
        </div>` : ""}
        ${message ? `<div class="small" style="margin-top:14px;color:${message.startsWith("Error") ? "var(--red)" : "var(--green)"}">${escapeHtml(message)}</div>` : ""}
      </div>
      <div class="modal-foot">
        <button class="btn ghost" data-modal-close>Cancel</button>
        <button class="btn accent" id="r-go" ${busy ? "disabled" : ""}>${icon("film", 12)}${busy ? "Rendering…" : "Render now"}</button>
      </div>`;
    }

    modal = openModal(html());
    function rerender() {
        modal.root.querySelector(".modal").innerHTML = html();
        wire();
    }
    function wire() {
        modal.root
            .querySelectorAll("[data-range]")
            .forEach((b) =>
                b.addEventListener("click", () => {
                    const v = b.dataset.range;
                    setRange(v === "all" ? "all" : Number(v));
                }),
            );
        modal.root.querySelectorAll("[data-fps]").forEach((b) =>
            b.addEventListener("click", () => {
                fps = Number(b.dataset.fps);
                rerender();
            }),
        );
        modal.root.querySelectorAll("[data-fmt]").forEach((b) =>
            b.addEventListener("click", () => {
                format = b.dataset.fmt;
                rerender();
            }),
        );

        modal.root.querySelector("#r-start-d").addEventListener("change", (e) => {
            startDate = e.target.value;
            activeRange = null;
            rerender();
        });
        modal.root.querySelector("#r-start-h").addEventListener("change", (e) => {
            startHour = Number(e.target.value);
            activeRange = null;
            rerender();
        });
        modal.root.querySelector("#r-end-d").addEventListener("change", (e) => {
            endDate = e.target.value;
            activeRange = null;
            rerender();
        });
        modal.root.querySelector("#r-end-h").addEventListener("change", (e) => {
            endHour = Number(e.target.value);
            activeRange = null;
            rerender();
        });

        modal.root
            .querySelector("#r-go")
            ?.addEventListener("click", async () => {
                busy = true;
                progress = 0;
                message = "";
                rerender();
                try {
                    const startAt = `${startDate}T${startHour.toString().padStart(2, "0")}:00:00`;
                    const endAt = `${endDate}T${endHour.toString().padStart(2, "0")}:59:59`;
                    const response = await fetch(`/api/cameras/${encId}/videos`, {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ start_at: startAt, end_at: endAt, fps, format }),
                    });
                    if (!response.ok || !response.body) {
                        throw new Error((await response.text()) || "Failed to render video");
                    }
                    const reader = response.body.getReader();
                    const decoder = new TextDecoder();
                    let buffer = "";
                    while (true) {
                        const { value, done } = await reader.read();
                        if (done) break;
                        buffer += decoder.decode(value, { stream: true });
                        const events = buffer.split("\n\n");
                        buffer = events.pop() || "";
                        for (const rawEvent of events) {
                            const lines = rawEvent.split("\n");
                            const eventType = lines.find((l) => l.startsWith("event:"))?.slice(6).trim();
                            const dataLine = lines.find((l) => l.startsWith("data:"))?.slice(5).trim();
                            if (!eventType || !dataLine) continue;
                            const data = JSON.parse(dataLine);
                            if (eventType === "progress") {
                                progress = data.percent;
                                rerender();
                            } else if (eventType === "done") {
                                message = `Rendered ${data.path}`;
                                busy = false;
                                progress = 100;
                                rerender();
                            } else if (eventType === "error") {
                                throw new Error(data.detail || "Render failed");
                            }
                        }
                    }
                } catch (e) {
                    busy = false;
                    message = "Error: " + e.message;
                    rerender();
                }
            });
    }
    wire();
}

registerView("#/library", renderLibrary);
