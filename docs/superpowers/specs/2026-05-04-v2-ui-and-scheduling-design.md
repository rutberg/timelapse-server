# v2 UI redesign + new scheduling modes

_Date: 2026-05-04_
_Source: handoff bundle from claude.ai/design (HbvNNb3mY0yjkXd237XruA)_

## Goals

Replace the current Pico-CSS / Alpine UI with the dark-themed v2 design from the design handoff bundle, and extend the capture scheduler to support three modes: **Daylight**, **Hours**, and **Scene light**. Scene light is the headline new feature — capture is gated by a luminance threshold measured from the camera's pre-capture frame.

## Non-goals

- Vendoring the prototype's React/Babel runtime (stack stays vanilla JS)
- Building out the Library or Settings views beyond what the design ships as stubs
- Implementing the Frames or Renders camera tabs beyond placeholder text
- Server-level configuration page (placeholder only)
- WebM render format (the format selector ships with MP4 and GIF only; WebM is removed)

## Architecture

The redesigned frontend lives at `server/app/static/v2/` and uses the same vanilla-JS-with-`registerView`-pattern as the current code. The current `server/app/static/` files are removed; the server's `index.html` route now serves `v2/index.html`. No build step.

Pico CSS and Alpine.js are removed entirely. They were already barely used.

Fonts: system stack only. The design's `<link rel="stylesheet" href="https://fonts.googleapis.com/...">` line is removed; CSS already declares `'Inter', -apple-system, system-ui, sans-serif` and `'JetBrains Mono', ui-monospace, monospace` as fallbacks. On modern macOS/Windows/Linux these render close enough to the prototype.

## Scheduling: three modes

The schedule control offers exactly three modes, plus a weekday picker that applies to all three.

### `hours` mode

Replaces the current 24-cell hour grid with a cleaner two-input start/end pair plus a visual track (sunrise gradient backdrop, draggable handles, "now" indicator). Wire format: `capture_hours` is the inclusive list of integers `[start..end-1]`, exactly the same format the agent already understands. No agent changes needed for this mode beyond honouring `schedule_days`.

### `daylight` mode (new)

Capture between sunrise and sunset for the camera's location, every day. Wire format: `capture_hours: null`, `schedule_mode: "daylight"`. The agent computes per-day sunrise/sunset from camera-configured `latitude`/`longitude` and treats hours outside that window as out-of-schedule.

When latitude/longitude are not set, daylight mode falls back to a fixed `[6..20)` local-hour window (06:00 to 20:00). The UI flags this state ("location not set — using 06:00–20:00 fallback").

Sunrise/sunset is computed inline in the agent using the standard NOAA solar position approximation (~30 lines of Python, no new dependency). Accuracy of ±1 minute is plenty for hour-level scheduling.

### `scene` mode (new — headline feature)

Capture only when the camera's pre-capture frame is bright enough. Wire format: `capture_hours: null`, `light_threshold: int (0–255)`, `schedule_mode: "scene"`.

Agent flow at each capture tick:

1. Capture a small low-res preview (e.g. 320×240) using the same `rpicam-still`/`libcamera-still`/`raspistill` tool already in the agent
2. Compute mean Y luma (0–255) by reading the JPEG pixel data
3. Report the reading as `current_light` in the next heartbeat
4. If `current_light >= light_threshold`, proceed with the full capture; otherwise skip and reschedule for the next interval

Mean Y is read directly from the camera tool's YUV output — see _Implementation notes_ for the exact path; it avoids any image-decoding dependency.

The UI shows a live light meter with the current `current_light` reading from the latest heartbeat plus the threshold marker, four named presets (Civil twilight=25, Overcast=60, Daylight=110, Bright sun=180), and a 0–255 slider.

### Weekday gating

Independent of mode. Wire format: `schedule_days: list[int] | None` where ints are ISO weekday (1=Mon..7=Sun). `null` means "every day" (the default).

## Data model changes

### `CameraConfig` — new fields

```python
schedule_mode: Literal["daylight", "hours", "scene"] | None  # None = legacy/always
schedule_days: list[int] | None                              # ISO 1..7; None = all days
light_threshold: int | None                                  # 0..255; required when mode="scene"
display_name: str | None                                     # human label, falls back to camera_id
latitude: float | None                                       # -90..90; for daylight mode
longitude: float | None                                      # -180..180; for daylight mode
```

`capture_hours` stays as-is. Validation rules:

- If `schedule_mode == "scene"`, `light_threshold` must be set; `capture_hours` is forced to `null`
- If `schedule_mode == "daylight"`, `capture_hours` is forced to `null`
- If `schedule_mode == "hours"`, `capture_hours` must be a non-empty list
- `latitude`/`longitude` are optional in all modes (only used by `daylight`)

### `CameraStatus` — new field

```python
current_light: int | None  # 0..255; last luma reading; null if scene mode never sampled
```

Reported by the agent in heartbeat. Stays sticky across heartbeats — only cleared if the camera moves out of scene mode.

### `GET /api/cameras` — new top-level field

```python
{
  "cameras": [...],  # unchanged shape, just with new config/status fields
  "stats": {
    "storage_bytes": int,           # bytes used under TIMELAPSE_DATA_DIR/images + videos
    "storage_capacity_bytes": int   # df -B1 reported total of that filesystem
  }
}
```

Used by the dashboard's storage stat card and the sidebar storage meter.

### New endpoint: `DELETE /api/cameras/:id`

Removes the camera from `config.json` and deletes its image/video directories on disk. Returns 204. The settings tab's "Delete camera" button calls this.

### `POST /api/cameras/:id/videos` — accept `format`

Body gains an optional `format: "mp4" | "gif"` (defaults to `"mp4"` for back-compat). GIF output uses ffmpeg's two-pass palette pipeline:

```
ffmpeg -framerate <fps> -i frames.txt -vf "scale=720:-1:flags=lanczos,palettegen" palette.png
ffmpeg -framerate <fps> -i frames.txt -i palette.png -filter_complex "scale=720:-1:flags=lanczos[x];[x][1:v]paletteuse" out.gif
```

Output filename gets the matching extension (`.mp4` or `.gif`).

## Agent changes

1. **Honour `schedule_days`** in `is_in_schedule()` alongside `capture_hours`
2. **Implement `daylight` mode**: compute sunrise/sunset locally; treat as `capture_hours = list(range(sunrise_hour, sunset_hour))`; fall back to `[6..20)` if lat/lon missing
3. **Implement `scene` mode**: pre-capture luma sample, compare to threshold, skip if below
4. **Report `current_light`** in heartbeat on every capture tick (whether captured or skipped)
5. **Optional: report `signal_dbm`** in heartbeat by reading `iwconfig` / `iw dev wlan0 link` on Linux. The detail page renders bars based on this; if unset, shows "no signal".

The luma sampling reuses existing capture tooling at low resolution. Pseudocode:

```python
def sample_light_level() -> int | None:
    # rpicam-still --width 64 --height 48 --encoding yuv420 -o - writes raw
    # YUV to stdout. The first width*height bytes are the Y plane.
    raw = run_capture_yuv(width=64, height=48)
    if not raw:
        return None
    plane = raw[: 64 * 48]
    return sum(plane) // len(plane)
```

## Frontend integration

The full v2 tree from the handoff bundle is copied into `server/app/static/v2/` with these adjustments:

1. **Remove the Google Fonts `<link>`** in `v2/index.html`
2. **Render modal**: keep the MP4 and GIF buttons; remove WebM
3. **Connection signal block** in camera detail uses `status.signal_dbm`; if absent, the bars render as "no signal" — no UI changes needed, the design already handles null

The current `server/app/static/index.html`, `app.js`, `styles.css`, and `views/*.js` are deleted. The FastAPI route that serves `index.html` is updated to serve `v2/index.html`.

## Files touched

```
server/app/main.py                       # config schema, /api/cameras stats, DELETE endpoint, route to v2/index.html
server/app/static/                       # current files removed
server/app/static/v2/index.html          # font link removed
server/app/static/v2/app.js              # copy from bundle
server/app/static/v2/styles.css          # copy from bundle
server/app/static/v2/components/sidebar.js
server/app/static/v2/components/schedule.js
server/app/static/v2/views/dashboard.js
server/app/static/v2/views/camera.js
server/app/static/v2/views/create-agent.js
server/app/static/v2/views/library.js    # render modal: mp4-only
server/app/static/v2/views/settings.js
agent/timelapse_agent.py                 # daylight calc, scene mode, schedule_days, current_light, signal_dbm
agent/VERSION                            # bump
tests/server/                            # tests for new fields, scene mode validation, DELETE
tests/agent/                             # tests for daylight calc, luma sampling, schedule_days gating
```

## Testing

- **Server**: schedule_mode validation (each mode's invariants), DELETE endpoint, /api/cameras stats block
- **Agent**: sunrise/sunset calc against known reference values, schedule_days gating, scene mode skip-vs-capture decision, mean Y extraction from a synthetic JPEG
- **Manual smoke**: load each view, switch schedule modes on a real camera config, verify the saved payload round-trips

## Implementation notes

**Mean Y without an image-decoding dependency.** `rpicam-still --width 64 --height 48 --encoding yuv420 -o -` writes raw YUV to stdout; the first `width*height` bytes are the Y plane (luminance, 0–255 per pixel). Mean is one `sum() // len()` call. `libcamera-still` accepts the same flags; `raspistill` uses `--encoding yuv` with the same plane order. If none of the YUV paths are available (very old userspace), scene mode is unavailable and the agent reports `current_light: null`.

Resolution choice: 64×48 = 3072 Y samples is enough to be robust to noise and fast (<200 ms on a Pi Zero). The thumbnail is discarded after sampling.

**Sunrise/sunset.** NOAA-style approximation, latitude/longitude/day-of-year → sunrise/sunset hours. Handles polar regions by saturating to "always on" or "always off". The agent recomputes once a day at midnight local time.

## Decision log

- **Vanilla JS not Alpine.** The handoff design dropped Alpine entirely after re-reading the project; we follow that. The previous Alpine usage was a single `x-data` binding for hash routing — easily replaced.
- **System fonts not vendored fonts.** Vendoring Inter + JetBrains Mono is 4–6 woff2 files (~600 KB). System fallbacks are visually close enough on the platforms this dashboard runs on (developer machines), and they keep the offline-LAN constraint trivially satisfied.
- **MP4 + GIF for render; WebM dropped.** ffmpeg already in the pipeline handles both. GIF uses a two-pass `palettegen` / `paletteuse` filter chain for decent quality at typical timelapse sizes. WebM stubbed out of the modal — adds a third codec path for marginal benefit.
- **Lat/lon optional with fallback.** Daylight mode without lat/lon falls back to 06:00–20:00 rather than refusing to set the mode. Lower friction for first-time setup.
- **`current_light` reported every tick, not just in scene mode.** Cheap, gives the UI a live reading for camera tuning even when the user isn't in scene mode.
