# Storage Mode Toggle Design

**Date:** 2026-05-08
**Scope:** Add a `storage_mode` field to `CameraConfig` and expose an on/off toggle in the camera settings panel so operators can switch a camera between RAM-buffered and direct-to-SD pending queues without re-provisioning.

---

## Background

The 0.12.0 agent introduced a two-tier pending queue: captures land in a tmpfs RAM directory first, and failed uploads spill to the SD card. The mode is currently controlled by `ram_pending_dir` in the agent's local config (`/etc/timelapse-agent/config.json`), which requires re-provisioning to change.

This design moves the control server-side so operators can toggle it from the camera settings panel. The change propagates to the agent on its next config poll (~60 s). No agent restart or push infrastructure is required.

---

## Data Model

### `CameraConfig` — new field

```python
storage_mode: Optional[str] = None  # "ram" | "sd" | None
```

| Value | Meaning |
|-------|---------|
| `"ram"` | Use RAM-first two-tier queue (tmpfs → SD spill on failure) |
| `"sd"` | Force single-tier: write captures directly to `work_dir/pending/` on SD |
| `None` | Server does not override; agent uses whatever `ram_pending_dir` is in its local config (current behaviour, backward-compatible default) |

Validation: reject any value that is not `"ram"`, `"sd"`, or `None`.

---

## Server Changes

**File:** `server/app/main.py`

- Add `storage_mode: Optional[str] = None` to `CameraConfig` with a validator.
- No new endpoints: `GET /api/cameras/{id}/config` and `PUT /api/cameras/{id}/config` already carry the full config object.

---

## Agent Changes

**File:** `agent/timelapse_agent.py`

`run_agent` currently resolves `ram_dir`/`spill_dir` once at startup from local settings. Move the resolution into the config-poll block so it re-evaluates each time the server config is fetched.

Resolution logic (new helper or inline in poll block):

```python
def effective_pending_dirs(
    settings: Dict[str, Any],
    remote_config: Dict[str, Any],
    work_dir: Path,
) -> tuple[Path, Path]:
    mode = remote_config.get("storage_mode")
    if mode == "sd":
        pending = work_dir / "pending"
        return pending, pending
    # "ram", None, or any unrecognised value → use local config
    return resolve_pending_dirs(settings, work_dir)
```

On each config poll, compare new dirs against current; log at INFO level if the effective mode changes. At initial startup (before the first poll), dirs are resolved from local settings as today; the server override takes effect after the first successful config fetch. Update `ram_dir` and `spill_dir` variables used by `capture_frame`, `upload_pending`, `evict_pending`, and `measure_pending`.

**Edge case:** if the operator switches RAM→SD while frames sit in the tmpfs, those files will not be automatically migrated. They will be lost at the next service restart (when systemd tears down the tmpfs). This is acceptable: switching is a deliberate operator action and at most ~60 s of frames are at risk.

---

## Frontend Changes

**File:** `server/app/static/v2/views/camera.js`

### `renderSettingsTab()`

Add a "Storage" card below the existing "Capture" card:

```
┌─ Storage ────────────────────────────────────────┐
│  RAM buffer    [toggle ON/OFF]                   │
│  Captures land in RAM first; failed uploads      │
│  spill to SD card automatically.                 │
└──────────────────────────────────────────────────┘
```

- Toggle **ON** → `storage_mode = "ram"`
- Toggle **OFF** → `storage_mode = "sd"`
- If `storage_mode` is `null` in the config, render the toggle as **ON** (matches the default provisioned behaviour since 0.12.0)

### `wireSettings()`

Read the toggle state and include `storage_mode` in the PUT payload alongside the existing capture fields.

---

## Testing

- Unit test: `storage_mode` validator rejects invalid values, accepts `"ram"`, `"sd"`, `null`
- Unit test: `effective_pending_dirs` returns single-tier when `storage_mode == "sd"`, two-tier when `"ram"`, and delegates to `resolve_pending_dirs` when `None`
- Manual: toggle off on `tomatoes`, wait 60 s, confirm startup log shows single-tier paths; toggle back on, confirm RAM paths restored

---

## Out of Scope

- Max images in RAM before proactive spill (next iteration)
- Push-based immediate apply (requires SSH command channel not yet built)
- Per-camera tmpfs size configuration
