# DSLR Camera Settings Module — Design Spec

**Date:** 2026-05-05
**Branch:** feature/canon-r6-dslr-support
**Scope:** Expose gphoto2 camera settings in the server config and agent, with a conditional UI section shown only when the active backend is gphoto2.

---

## Overview

The existing gphoto2 backend captures images but exposes no camera controls — shutter speed, aperture, ISO, image format, and white balance are all left at whatever the camera body is currently set to. This spec adds:

1. **Typed data models** for DSLR settings (configurable) and DSLR status (telemetry + available choices).
2. **Agent behavior** to read available choices on startup, apply settings before each capture, and surface telemetry on each checkin.
3. **A conditional UI section** shown only when the camera uses the gphoto2 backend, with a read-only telemetry block and two settings sub-sections (initialization and capture).

---

## Data Models

### `DslrSettings` (new Pydantic model, lives inside `CameraConfig`)

Stored in `data/config.json` as `cameras[id].config.dslr`.

```python
class DslrSettings(BaseModel):
    # Startup/init settings — applied once on agent init and on re-init
    capture_target: str = "Memory card"   # "Internal RAM" | "Memory card"
    drive_mode: str = "Single"
    focus_mode: str = "Manual"

    # Sequence settings — applied before each capture; None = leave camera as-is
    shutterspeed: Optional[str] = None
    aperture: Optional[str] = None
    iso: Optional[str] = None
    exposure_compensation: Optional[str] = None
    whitebalance: Optional[str] = None
    image_format: Optional[str] = None

    # Re-init signal: UI sets to ISO timestamp; agent echoes it back in DslrStatus
    reinit_token: Optional[str] = None
```

Added to `CameraConfig` as:
```python
dslr: Optional[DslrSettings] = None
```
`None` when `camera_backend` is not `"gphoto2"`. The server does not enforce this constraint — the agent ignores `dslr` when the active backend is not gphoto2.

### `DslrStatus` (new Pydantic model, lives inside `CameraStatus`)

Populated by the agent on each checkin.

```python
class DslrStatus(BaseModel):
    # Read-only telemetry
    battery_level: Optional[str] = None
    available_shots: Optional[int] = None
    shutter_counter: Optional[int] = None
    exposure_mode: Optional[str] = None   # e.g. "M", "Av", "Tv"

    # Available choices per configurable setting key.
    # Keys are gphoto2 widget names (e.g. "exposurecompensation", "imageformat", "capturetarget").
    # e.g. {"iso": ["Auto", "100", "200", ...], "shutterspeed": ["bulb", "30", ..., "1/8000"]}
    choices: dict[str, list[str]] = {}

    # Echo of the last reinit_token the agent applied
    last_reinit_token: Optional[str] = None
    last_init_at: Optional[str] = None    # ISO timestamp
```

Added to `CameraStatus` as:
```python
dslr: Optional[DslrStatus] = None
```

`CameraStatus` also gains:
```python
active_backend: Optional[str] = None  # "rpicam" | "gphoto2" | None — resolved by agent at startup
```
The agent already calls `resolve_active_backend()` at startup; it should include the result in every checkin payload. This field is what the UI uses to detect `auto`-resolved gphoto2 cameras.

---

## Agent Behavior

All changes are in `agent/timelapse_agent.py`. No new dependencies.

### New helper functions

**`gphoto2_read_choices(keys: list[str]) -> dict[str, list[str]]`**
- Calls `gphoto2 --get-config <key>` for each key in the list.
- Parses lines starting with `Choice:` to extract the value list.
- Returns a dict keyed by the setting name. Missing or failed keys are omitted.
- Called once on startup (and again on re-init). Result cached in `AgentState.dslr_choices`.

Setting keys to read choices for: `shutterspeed`, `aperture`, `iso`, `exposurecompensation`, `whitebalance`, `imageformat`, `capturetarget`, `drivemode`, `focusmode`.

**`gphoto2_read_telemetry() -> DslrStatus`**
- Calls `gphoto2 --get-config batterylevel availableshots shuttercounter autoexposuremode` (batch call with multiple keys).
- Parses `Current:` line for each.
- Returns a partial `DslrStatus` (telemetry fields only; `choices` and reinit fields filled in separately).
- Called once per main loop iteration, after `capture_frame`.

**`gphoto2_apply_init_settings(dslr: DslrSettings)`**
- Applies the three startup settings via individual `--set-config` calls:
  - `capturetarget` → `dslr.capture_target`
  - `drivemode` → `dslr.drive_mode`
  - `focusmode` → `dslr.focus_mode`
- Failures are logged at WARNING level and swallowed (same pattern as `autopoweroff`).

**`gphoto2_apply_sequence_settings(dslr: DslrSettings)`**
- For each of the six sequence fields, if the value is not `None`, calls `gphoto2 --set-config <key>=<value>`.
- Keys: `shutterspeed`, `aperture`, `iso`, `exposurecompensation`, `whitebalance`, `imageformat`.
- Failures per-key are logged and swallowed; one bad value does not abort the others.

### Startup sequence (additions to `run_agent`)

After `gphoto2_disable_autopoweroff()`:
1. Call `gphoto2_read_choices(...)` → store in `state.dslr_choices`.
2. Call `gphoto2_apply_init_settings(config.dslr)`.
3. Set `state.last_reinit_token = config.dslr.reinit_token`.

### Main loop additions

**Re-init detection** (after `fetch_remote_config`):
```python
if backend == "gphoto2" and config.dslr:
    if config.dslr.reinit_token != state.last_reinit_token:
        gphoto2_apply_init_settings(config.dslr)
        state.dslr_choices = gphoto2_read_choices(...)
        state.last_reinit_token = config.dslr.reinit_token
        state.last_init_at = utcnow_iso()
```

**Sequence settings application** (before `capture_frame`):
```python
if backend == "gphoto2" and config.dslr:
    gphoto2_apply_sequence_settings(config.dslr)
```

**Telemetry** (after `capture_frame`, before checkin):
```python
if backend == "gphoto2":
    state.dslr_telemetry = gphoto2_read_telemetry()
```

### Checkin payload additions

The `DslrStatus` reported in checkin is assembled from:
- `state.dslr_telemetry` (live reads)
- `state.dslr_choices` (cached from last init)
- `state.last_reinit_token` and `state.last_init_at`

---

## API

No new endpoints. Changes flow through existing routes:

| Route | Change |
|---|---|
| `GET /api/cameras/{id}/config` | Returns `dslr` field from `CameraConfig` |
| `PUT /api/cameras/{id}/config` | Accepts `dslr` field; validates via `DslrSettings` |
| `POST /api/cameras/{id}/checkin` | Accepts `dslr` field in status body; stores in `CameraStatus` |
| `GET /api/cameras` | Passes through `dslr` in both config and status summaries |

---

## UI

All changes in `server/app/static/v2/views/camera.js`.

### Visibility condition

The DSLR settings section renders if:
```js
camera.config.camera_backend === "gphoto2" ||
camera.status?.active_backend === "gphoto2"
```
This covers both explicit `gphoto2` config and `auto` resolved to gphoto2 at runtime.

When the DSLR section is visible, `image_width`, `image_height`, and `jpeg_quality` inputs are hidden (they are no-ops for gphoto2).

### Layout

```
┌─ DSLR Status ──────────────────────────────────────┐
│  Battery: 87%   Available shots: 1,204              │
│  Shutter count: 12,483                              │
│  Exposure mode: [M]  ← green badge                 │
│  (or: Exposure mode: [Av] ← amber warning)          │
└─────────────────────────────────────────────────────┘

┌─ Camera Initialization ─────────────────────────────┐
│  Capture target: [Memory card ▾]                    │
│  Drive mode:     [Single       ▾]                   │
│  Focus mode:     [Manual       ▾]                   │
│                                                     │
│  [Re-initialize]  ← button; spinner while pending   │
└─────────────────────────────────────────────────────┘

┌─ Capture Settings ──────────────────────────────────┐
│  Shutter speed:  [— (leave as-is) ▾]               │
│  Aperture:       [— (leave as-is) ▾]               │
│  ISO:            [400            ▾]                 │
│  Exposure comp:  [0              ▾]                 │
│  White balance:  [Daylight       ▾]                 │
│  Image format:   [Large Fine JPEG▾]                 │
└─────────────────────────────────────────────────────┘
```

### Dropdowns

- **Initialization dropdowns**: hard-coded choices (stable across Canon bodies).
  - `capture_target`: `Internal RAM`, `Memory card`
  - `drive_mode`: `Single`, `Continuous`, `Self Timer 2 sec`, `Self Timer 10 sec`
  - `focus_mode`: `Manual`, `One Shot`, `AI Servo`, `Single`, `Servo`
- **Capture setting dropdowns**: populated from `camera.status.dslr.choices[key]` when available; fall back to embedded Canon R6 defaults while agent hasn't checked in yet.
- All capture setting dropdowns include a blank `""` option labelled "— (leave as-is)" at the top.

### Re-initialize button

- Always clickable.
- On click: sets `config.dslr.reinit_token = new Date().toISOString()`, then PUTs the config via the existing save handler.
- Shows a spinner badge while `camera.config.dslr.reinit_token !== camera.status.dslr?.last_reinit_token`.
- Shows a checkmark badge once tokens match.
- Pending state survives a page refresh (tokens are persisted in server config and status).

### Save flow

DSLR fields are included in the existing `wireSettings` PUT handler. No new save button or endpoint — DSLR settings save alongside all other camera config fields.

---

## Error handling

| Scenario | Handling |
|---|---|
| gphoto2 not installed | `gphoto2_read_choices` returns `{}`; no settings applied; telemetry is `None` |
| Camera disconnected mid-sequence | `--set-config` subprocess fails; logged at WARNING; capture proceeds (may fail too) |
| Invalid setting value | `--set-config` returns non-zero; logged at WARNING; other settings still applied |
| Camera in wrong mode for a setting | Same as invalid value — gphoto2 returns error, logged, swallowed |
| `choices` empty on UI load | Fallback to hard-coded Canon R6 value lists |

---

## Out of scope

- Live-view light sampling for gphoto2 (deferred from original DSLR plan)
- Non-Canon camera bodies (choices fallback lists are Canon R6 values)
- RAW+JPEG dual-file capture
- Per-capture exposure ramping (holy grail timelapse)
- Camera firmware update or custom function config
