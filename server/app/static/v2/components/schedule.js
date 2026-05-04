// Schedule control — three modes (Daylight / Hours / Scene light) plus a weekday picker.
//
// Wire format:
//   schedule_mode:    "daylight" | "hours" | "scene"
//   capture_hours:    number[] | null   (24h ints, 0–23) — only when mode = "hours"
//   schedule_days:    number[] | null   (ISO weekday ints, 1=Mon..7=Sun) — null = all days
//   light_threshold:  number | null     (0–255 mean Y) — only when mode = "scene"
//
// Modes:
//   • "daylight" — auto-derive capture window from sunrise/sunset for the
//                  agent's configured location. Server returns capture_hours: null
//                  and the agent computes per day.
//   • "hours"    — explicit start/end hour pair.  capture_hours = [start..end-1]
//   • "scene"    — capture only when the pre-capture frame's mean luminance
//                  exceeds light_threshold. Independent of clock time.
//                  capture_hours is null; light_threshold is set.

import { icon, escapeHtml } from "/static/v2/app.js";

const DAY_LABELS = { 1: "M", 2: "T", 3: "W", 4: "T", 5: "F", 6: "S", 7: "S" };
const DAY_KEYS   = [1, 2, 3, 4, 5, 6, 7];
const PRESETS = [
  { k: "all", l: "Every day", days: [1,2,3,4,5,6,7] },
  { k: "wd",  l: "Weekdays",  days: [1,2,3,4,5] },
  { k: "we",  l: "Weekends",  days: [6,7] },
];

const LIGHT_PRESETS = [
  { l: "Civil twilight", v: 25 },
  { l: "Overcast",       v: 60 },
  { l: "Daylight",       v: 110 },
  { l: "Bright sun",     v: 180 },
];

/**
 * @param {object} opts
 * @param {{capture_hours: number[]|null, schedule_days: number[]|null,
 *          schedule_mode?: string, light_threshold?: number|null,
 *          current_light?: number|null}} opts.value
 * @param {(value: object) => void} opts.onChange  – fires whenever the user edits
 */
export function mountScheduleControl(host, opts) {
  const state = {
    mode:       deriveMode(opts.value),
    rangeStart: deriveRangeStart(opts.value),
    rangeEnd:   deriveRangeEnd(opts.value),
    threshold:  opts.value?.light_threshold ?? 60,
    currentLight: opts.value?.current_light ?? null,
    days:       new Set(opts.value?.schedule_days || [1,2,3,4,5,6,7]),
  };

  function emit() {
    opts.onChange?.(serialize(state));
  }

  function render() {
    host.innerHTML = `
      <div class="card">
        <div class="card-h">
          <div>
            <div class="lbl">Capture schedule</div>
            <div class="mono small" style="margin-top:2px">${describe(state)}</div>
          </div>
          <div class="seg" data-mode-seg>
            <button data-mode="daylight" class="${state.mode === "daylight" ? "active" : ""}">Daylight</button>
            <button data-mode="hours"    class="${state.mode === "hours"    ? "active" : ""}">Hours</button>
            <button data-mode="scene"    class="${state.mode === "scene"    ? "active" : ""}">Scene light</button>
          </div>
        </div>
        <div class="card-b">
          ${state.mode === "daylight" ? renderDaylight(state) : ""}
          ${state.mode === "hours"    ? renderHours(state)    : ""}
          ${state.mode === "scene"    ? renderScene(state)    : ""}
          ${renderDayPicker(state)}
        </div>
      </div>`;

    // Mode tabs
    host.querySelectorAll("[data-mode]").forEach(btn => {
      btn.addEventListener("click", () => {
        state.mode = btn.dataset.mode;
        render(); emit();
      });
    });

    // Hours-mode start/end inputs
    host.querySelectorAll("[data-range-input]").forEach(inp => {
      inp.addEventListener("input", () => {
        const v = clampHour(parseInt(inp.value, 10));
        if (inp.dataset.rangeInput === "start") state.rangeStart = Math.min(v, state.rangeEnd - 1);
        else state.rangeEnd = Math.max(v, state.rangeStart + 1);
        render(); emit();
      });
    });

    // Scene-mode threshold slider
    host.querySelector("[data-threshold]")?.addEventListener("input", (e) => {
      state.threshold = clampByte(parseInt(e.target.value, 10));
      render(); emit();
    });
    host.querySelectorAll("[data-threshold-preset]").forEach(btn => {
      btn.addEventListener("click", () => {
        state.threshold = Number(btn.dataset.thresholdPreset);
        render(); emit();
      });
    });

    // Day pills
    host.querySelectorAll("[data-day]").forEach(btn => {
      btn.addEventListener("click", () => {
        const d = Number(btn.dataset.day);
        if (state.days.has(d)) {
          if (state.days.size <= 1) return;  // refuse: would leave no days enabled
          state.days.delete(d);
        } else {
          state.days.add(d);
        }
        render(); emit();
      });
    });

    // Day presets
    host.querySelectorAll("[data-day-preset]").forEach(btn => {
      btn.addEventListener("click", () => {
        const preset = PRESETS.find(p => p.k === btn.dataset.dayPreset);
        if (!preset) return;
        state.days = new Set(preset.days);
        render(); emit();
      });
    });
  }

  render();

  return {
    setValue(value) {
      state.mode       = deriveMode(value);
      state.rangeStart = deriveRangeStart(value);
      state.rangeEnd   = deriveRangeEnd(value);
      state.threshold  = value?.light_threshold ?? 60;
      state.currentLight = value?.current_light ?? null;
      state.days       = new Set(value?.schedule_days || [1,2,3,4,5,6,7]);
      render();
    },
    /** Live-update the scene-light reading without resetting other state. */
    setCurrentLight(value) {
      state.currentLight = value;
      if (state.mode === "scene") render();
    },
    getValue() { return serialize(state); },
  };
}

// ---- Sub-renders -------------------------------------------------------------

function renderDaylight(state) {
  const sunrise = 5.5;
  const sunset  = 21.0;
  const pct = (h) => `${(h / 24) * 100}%`;
  const nowH = new Date().getHours() + new Date().getMinutes() / 60;

  return `
    <div class="banner" style="background:var(--bg);border-color:var(--border)">
      ${icon("sun", 14)}
      <div class="small" style="line-height:1.55">
        Capture from sunrise to sunset, every day. The agent computes its own sunrise/sunset based on its configured location — no manual hour-picking. Below the strip is a preview using today's reference times.
      </div>
    </div>
    <div class="sched-track" style="margin-top:14px;background:linear-gradient(to right,
      #1a1c20 0%, #1a1c20 ${pct(sunrise-1)}, #2c3142 ${pct(sunrise-0.5)},
      #4a5670 ${pct(sunrise+0.5)}, #c47c4a ${pct(sunrise+1)},
      #6a8aab ${pct(12)},
      #c47c4a ${pct(sunset-1)}, #4a5670 ${pct(sunset)},
      #2c3142 ${pct(sunset+0.5)}, #1a1c20 ${pct(sunset+1)}, #1a1c20 100%)">
      <div class="sched-window" style="left:${pct(sunrise)};right:calc(100% - ${pct(sunset)})"></div>
      <div class="sched-now" style="left:${pct(nowH)}"></div>
      <div style="position:absolute;left:${pct(sunrise)};top:4px;transform:translateX(-50%);font-family:var(--mono);font-size:9px;color:var(--accent-2)">${formatHour(sunrise)}</div>
      <div style="position:absolute;left:${pct(sunset)};top:4px;transform:translateX(-50%);font-family:var(--mono);font-size:9px;color:var(--accent-2)">${formatHour(sunset)}</div>
    </div>
    <div class="sched-axis">
      ${[0,3,6,9,12,15,18,21,24].map(h => `<span>${h}:00</span>`).join("")}
    </div>`;
}

function renderHours(state) {
  const pct = (h) => `${(h / 24) * 100}%`;
  const nowH = new Date().getHours() + new Date().getMinutes() / 60;
  return `
    <div class="sched-track" style="background:linear-gradient(to right, #1a1c20 0%, #2c3142 25%, #6a8aab 50%, #2c3142 75%, #1a1c20 100%)">
      <div class="sched-window" style="left:${pct(state.rangeStart)};right:calc(100% - ${pct(state.rangeEnd)})"></div>
      <div class="sched-now" style="left:${pct(nowH)}"></div>
      <div class="sched-handle" style="left:${pct(state.rangeStart)}"></div>
      <div class="sched-handle" style="left:${pct(state.rangeEnd)}"></div>
    </div>
    <div class="sched-axis">
      ${[0,3,6,9,12,15,18,21,24].map(h => `<span>${h}:00</span>`).join("")}
    </div>
    <div class="row" style="margin-top:14px;gap:24px;flex-wrap:wrap">
      <div class="col" style="gap:4px">
        <span class="lbl">Start hour</span>
        <input class="input" data-range-input="start" type="number" min="0" max="22" value="${state.rangeStart}" style="width:80px"/>
      </div>
      <div class="col" style="gap:4px">
        <span class="lbl">End hour</span>
        <input class="input" data-range-input="end" type="number" min="1" max="24" value="${state.rangeEnd}" style="width:80px"/>
      </div>
      <div class="col" style="gap:4px">
        <span class="lbl">Window</span>
        <span class="num" style="font-size:13px;padding:6px 0">${state.rangeEnd - state.rangeStart} h/day</span>
      </div>
    </div>
    <div class="small" style="margin-top:8px">Captures only between <span class="mono" style="color:var(--ink)">${formatHour(state.rangeStart)}</span> and <span class="mono" style="color:var(--ink)">${formatHour(state.rangeEnd)}</span> agent local time.</div>`;
}

function renderScene(state) {
  // currentLight may be null when no live reading is available; fall back to a
  // neutral value just for the UI marker, but tag the state honestly.
  const hasReading = state.currentLight != null && Number.isFinite(state.currentLight);
  const reading = hasReading ? state.currentLight : null;
  const isAbove = hasReading && reading >= state.threshold;
  const thrPct  = (state.threshold / 255) * 100;
  const nowPct  = hasReading ? (reading / 255) * 100 : null;

  return `
    <div class="banner" style="background:var(--bg);border-color:var(--border)">
      ${icon("sun", 14)}
      <div class="small" style="line-height:1.55">
        Capture only when the scene is bright enough. The agent samples the
        sensor's <strong>average luminance</strong> (mean Y from the 1-second
        pre-capture frame, 0–255) before each scheduled shot — if it's below
        your threshold the frame is skipped.
      </div>
    </div>

    <div style="margin-top:14px">
      <div class="between">
        <div class="lbl">Current scene light</div>
        <span class="mono" style="font-size:11px;color:${
          !hasReading ? "var(--soft)" : isAbove ? "var(--green)" : "var(--soft)"
        }">
          ${hasReading
            ? `Y\u0304 = ${reading} \u00b7 ${isAbove ? "capturing" : "skipped"}`
            : "Y\u0304 = \u2014 \u00b7 awaiting first reading"}
        </span>
      </div>
      <div style="position:relative;margin-top:14px;height:32px;background:linear-gradient(to right, #0a0b0e 0%, #2c3142 25%, #6a8aab 50%, #d9bf7e 75%, #f4e4b8 100%);border:1px solid var(--border);border-radius:4px">
        <div style="position:absolute;left:${thrPct}%;top:-4px;bottom:-4px;width:2px;background:var(--accent-2);box-shadow:0 0 0 1px rgba(0,0,0,.3)"></div>
        <div style="position:absolute;left:${thrPct}%;top:-16px;transform:translateX(-50%);font-family:var(--mono);font-size:9px;color:var(--accent-2);white-space:nowrap">
          threshold ${state.threshold}
        </div>
        ${nowPct != null ? `
          <div style="position:absolute;left:${nowPct}%;top:-2px;bottom:-2px;width:2px;background:var(--ink)"></div>
          <div style="position:absolute;left:${nowPct}%;bottom:-16px;transform:translateX(-50%);font-family:var(--mono);font-size:9px;color:${isAbove ? "var(--green)" : "var(--soft)"};white-space:nowrap">
            now ${reading}
          </div>` : ""}
      </div>
      <div class="row" style="margin-top:22px;gap:8px;font-family:var(--mono);font-size:10px;color:var(--soft)">
        <span>0 night</span>
        <span style="margin-left:auto">twilight</span>
        <span style="margin-left:auto">overcast</span>
        <span style="margin-left:auto">full sun 255</span>
      </div>
    </div>

    <div style="margin-top:18px">
      <div class="lbl">Threshold</div>
      <div class="row" style="margin-top:6px;gap:10px;align-items:center">
        <input data-threshold type="range" min="0" max="255" value="${state.threshold}"
               style="flex:1;accent-color:var(--accent-2)"/>
        <span class="mono" style="min-width:36px;text-align:right;font-size:12px">${state.threshold}</span>
      </div>
      <div class="row" style="margin-top:8px;gap:4px;flex-wrap:wrap">
        ${LIGHT_PRESETS.map(p => `
          <button class="btn ghost sm ${state.threshold === p.v ? "active" : ""}"
                  data-threshold-preset="${p.v}">
            ${escapeHtml(p.l)}
            <span class="mono" style="margin-left:4px;color:var(--soft)">${p.v}</span>
          </button>`).join("")}
      </div>
    </div>`;
}

function renderDayPicker(state) {
  return `
    <div style="margin-top:18px;padding-top:14px;border-top:1px solid var(--border)">
      <div class="between">
        <div class="lbl">Active days</div>
        <div class="row" style="gap:4px">
          ${PRESETS.map(p => `<button class="btn ghost sm" data-day-preset="${p.k}">${p.l}</button>`).join("")}
        </div>
      </div>
      <div class="row" style="margin-top:8px;gap:4px">
        ${DAY_KEYS.map(d => {
          const on = state.days.has(d);
          const style = `border:1px solid ${on ? "var(--accent-2)" : "var(--border-strong)"};
                         background:${on ? "rgba(245,176,72,.12)" : "var(--panel)"};
                         color:${on ? "var(--accent-2)" : "var(--soft)"};
                         font-weight:${on ? "600" : "400"}`;
          return `<button class="day-pill" data-day="${d}" style="${style}">${DAY_LABELS[d]}</button>`;
        }).join("")}
      </div>
      <div class="mono small" style="margin-top:6px">
        ${state.days.size === 7 ? "Every day" :
          `${state.days.size} day${state.days.size === 1 ? "" : "s"} per week`}
      </div>
    </div>`;
}

// ---- Helpers -----------------------------------------------------------------

function deriveMode(value) {
  // Explicit mode wins.
  if (value?.schedule_mode === "daylight" ||
      value?.schedule_mode === "hours" ||
      value?.schedule_mode === "scene") return value.schedule_mode;
  // Back-compat: a populated light_threshold without explicit mode = scene.
  if (value?.light_threshold != null) return "scene";
  // Back-compat: capture_hours present = hours mode; absent = daylight.
  if (value?.capture_hours && value.capture_hours.length) return "hours";
  return "daylight";
}

function deriveRangeStart(value) {
  const hrs = value?.capture_hours;
  if (!hrs || !hrs.length) return 6;
  return Math.min(...hrs);
}

function deriveRangeEnd(value) {
  const hrs = value?.capture_hours;
  if (!hrs || !hrs.length) return 20;
  // capture_hours is inclusive; UI uses an exclusive end hour for clarity
  return Math.max(...hrs) + 1;
}

function rangeToList(start, end) {
  const out = [];
  for (let h = start; h < end; h++) out.push(h);
  return out;
}

function serialize(state) {
  const days = state.days.size === 7 ? null : [...state.days].sort((a,b)=>a-b);
  if (state.mode === "daylight") {
    return { schedule_mode: "daylight", capture_hours: null, schedule_days: days, light_threshold: null };
  }
  if (state.mode === "hours") {
    return {
      schedule_mode: "hours",
      capture_hours: rangeToList(state.rangeStart, state.rangeEnd),
      schedule_days: days,
      light_threshold: null,
    };
  }
  // scene
  return {
    schedule_mode: "scene",
    capture_hours: null,
    schedule_days: days,
    light_threshold: state.threshold,
  };
}

function describe(state) {
  const days = state.days.size === 7 ? "every day"
             : state.days.size === 0 ? "(no days)"
             : `${state.days.size}/7 days`;
  if (state.mode === "daylight") return `Daylight only · ${days}`;
  if (state.mode === "hours") {
    const span = state.rangeEnd - state.rangeStart;
    return `${formatHour(state.rangeStart)} → ${formatHour(state.rangeEnd)} · ${span}h/day · ${days}`;
  }
  // scene
  return `Scene-light gated · threshold ${state.threshold} · ${days}`;
}

function formatHour(h) {
  const hh = Math.floor(h);
  const mm = Math.round((h - hh) * 60);
  return `${String(hh).padStart(2, "0")}:${String(mm).padStart(2, "0")}`;
}

function clampHour(h) {
  if (Number.isNaN(h)) return 0;
  return Math.max(0, Math.min(24, h));
}

function clampByte(v) {
  if (Number.isNaN(v)) return 0;
  return Math.max(0, Math.min(255, v));
}
