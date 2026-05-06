# Sketch — Per-camera DSLR/PTP property discovery

Tracking issue: [#13 — Per-camera DSLR/PTP property discovery wizard](https://github.com/rutberg/timelapse/issues/13)

Status: **draft sketch**, not yet implemented. Goal of this doc is to lock the
shape of the data model, the agent ↔ server protocol, and the UI before any
code lands.

## Problem (from #13, condensed)

Today the agent and the camera-settings UI both hardcode a Canon-flavored set
of `gphoto2` PTP properties:

- `agent/timelapse_agent.py:848` (`gphoto2_read_telemetry`) reads
  `batterylevel`, `availableshots`, `shuttercounter`, `lensname`,
  `cameramodel`, with one fallback (`autoexposuremode → expprogram`).
- `agent/timelapse_agent.py:741` (`_DSLR_CHOICE_KEYS`) lists six
  `capturesettings`/`imgsettings` keys that the agent reads choices for.
- `server/app/static/v2/views/camera.js:1676` (`renderDslrSection`) renders
  fixed Battery / Available shots / Shutter count tiles plus six fixed
  capture-setting dropdowns.

Sony DSC-RX100M5A exposes a different subset (no battery / shots / shutter
counter / lens name; uses `expprogram` instead of `autoexposuremode`); Nikon
and Fuji bodies will be different again. The follow-up comment on the issue
explicitly calls out Sony + Nikon support.

We need a one-time **discovery step** per camera that asks the body what it
exposes, stores a per-body **property map** on the camera record, and then
drives both telemetry reads and the settings UI from that map instead of
hardcoded keys.

## Non-goals

- Cross-vendor *value* normalisation (e.g. translating Sony `"32/10"` to
  Canon-style `"3.2"`). Out of scope, per the issue.
- `rpicam` discovery. Out of scope, per the issue.
- Live re-discovery on every check-in. Discovery is a wizard step; the map
  is editable post-provision but not auto-refreshed.

## Architecture at a glance

```
┌────────────────┐  1. POST /discover/start            ┌──────────────────┐
│ Camera UI      │ ─────────────────────────────────▶  │ Server           │
│ (camera.js)    │                                     │ (main.py)        │
│                │ ◀────────────────────────────────── │                  │
│                │  2. discovery_token + "pending"     │                  │
└────────────────┘                                     └────────┬─────────┘
                                                                │ stores
                                                                │ pending_discovery
                                                                │ in camera record
┌────────────────┐  3. agent polls /api/.../config              ▼
│ Agent          │ ─────────────────────────────────▶  ┌──────────────────┐
│ (timelapse_    │                                     │ remote_config now│
│  agent.py)     │ ◀────────────────────────────────── │ carries          │
│                │  pending_discovery: {token}         │ pending_discovery│
│                │                                     └────────┬─────────┘
│  4. gphoto2 --list-config → POST /discover/result            │
│ ─────────────────────────────────────────────────▶            │
└────────────────┘                                              │
                                                                │ stores
                                                                ▼
                                                       dslr_property_map
                                                       (camera record)

┌────────────────┐  5. GET /discover/proposal          ┌──────────────────┐
│ Camera UI      │ ─────────────────────────────────▶  │ Server proposes  │
│ wizard         │  show proposed status tiles +       │ status_tiles +   │
│                │  setting dropdowns, user edits ─▶   │ setting_keys     │
│                │  PUT /discover/finalize             │ defaults         │
└────────────────┘                                     └──────────────────┘
```

Steps 1–4 = discovery. Step 5 = user confirmation of the proposed mapping.
The result is `camera.config.dslr_property_map`, which the agent and
`renderDslrSection` both read going forward.

## Data model

### New: `camera.config.dslr_property_map`

Stored on the camera record. Shape:

```python
class DslrTelemetryTile(BaseModel):
    label: str                      # "Battery", "Available shots", ...
    gphoto_key: str                 # "batterylevel"
    kind: Literal["string", "int"]  # how to render / coerce

class DslrSettingDropdown(BaseModel):
    label: str                      # "Shutter speed"
    gphoto_key: str                 # "shutterspeed"
    settings_field: str             # snake_case key on DslrSettings
                                    # ("shutterspeed", "exposure_compensation", …)

class DslrPropertyMap(BaseModel):
    schema_version: int = 1
    discovered_at: str              # ISO timestamp from agent
    body: DslrBodyInfo              # vendor / model / serial
    telemetry_tiles: List[DslrTelemetryTile]
    setting_dropdowns: List[DslrSettingDropdown]
    init_keys: List[DslrInitKey]    # capturetarget / drivemode / focusmode
                                    # (still vendor-specific vocab)

class DslrBodyInfo(BaseModel):
    vendor: Optional[str]           # "Canon", "Sony", parsed from --auto-detect
    model: Optional[str]            # "Canon EOS R6"
    serial: Optional[str]           # serialnumber / eosserialnumber if exposed
```

### New: `camera.status.dslr_discovery`

Tracks an in-flight discovery handshake:

```python
class DslrDiscoveryStatus(BaseModel):
    token: Optional[str]            # UUID minted by server when wizard starts
    requested_at: Optional[str]
    completed_at: Optional[str]
    error: Optional[str]            # "no camera detected", parse error, etc.
    raw_keys_count: Optional[int]   # for "we saw 247 PTP keys" UI affordance
```

### New: `camera.config.dslr_pending_discovery`

The slot the agent reads from `remote_config` to know it should run a
discovery pass on its next loop iteration:

```python
class DslrPendingDiscovery(BaseModel):
    token: str                      # matches DslrDiscoveryStatus.token
    requested_at: str
```

Cleared by the server once the matching `/discover/result` lands.

### `DslrSettings` stays as-is for now

The existing `DslrSettings` model (`shutterspeed`, `aperture`, `iso`,
`exposure_compensation`, `whitebalance`, `image_format`,
`capture_target`, `drive_mode`, `focus_mode`, `reinit_token`) is the
*intersection* of what every supported body exposes. We keep it as the
canonical save shape; `setting_dropdowns` just decides which of those
slots the UI renders for *this* body. A Sony body that doesn't expose
`exposurecompensation` simply gets no dropdown for it, and the saved
value stays `None`.

(If we later need Sony-only fields like Multi-Frame NR, we extend
`DslrSettings` with optional fields and add them to that body's
`setting_dropdowns`.)

## Agent-side flow

New helper in `agent/timelapse_agent.py`:

```python
def gphoto2_list_config() -> List[Dict[str, Any]]:
    """Run `gphoto2 --list-all-config`; return [{key, label, type, choices,
    current}] for every PTP property the camera exposes."""
```

`--list-all-config` (vs `--list-config`) returns the choices and current
value in one pass for every key, which matches what we need.

Discovery trigger lives inside the existing config-poll loop
(`run_agent`, `timelapse_agent.py:1162`):

```python
if state.active_backend == "gphoto2":
    pending = remote_config.get("dslr_pending_discovery")
    if pending and pending["token"] != state.last_discovery_token:
        try:
            full = gphoto2_list_config()
            post_discovery_result(settings, pending["token"], full)
            state.last_discovery_token = pending["token"]
        except Exception as e:
            post_discovery_result(settings, pending["token"], None, error=str(e))
```

Once `dslr_property_map` is set, the existing telemetry/choices reads
become driven by the map:

```python
def gphoto2_read_telemetry(prop_map: DslrPropertyMap) -> Dict[str, Any]:
    return {
        tile.gphoto_key: _coerce(_gphoto2_get_current(tile.gphoto_key), tile.kind)
        for tile in prop_map.telemetry_tiles
    }

def gphoto2_read_choices_and_current(prop_map: DslrPropertyMap):
    keys = [d.gphoto_key for d in prop_map.setting_dropdowns]
    keys += [k.gphoto_key for k in prop_map.init_keys]
    # … same loop as today, but driven by the map.
```

The hardcoded `_DSLR_CHOICE_KEYS` and the Canon-flavored fallback chain
in `_gphoto2_first_current` go away.

## Server-side endpoints

All scoped under the existing `/api/cameras/{camera_id}/...` namespace.

| Method | Path                                        | Caller   | Purpose |
|--------|---------------------------------------------|----------|---------|
| POST   | `.../dslr/discovery`                        | UI       | Mint discovery token; set `dslr_pending_discovery` on the config so the agent picks it up on next poll. Returns `{token}`. |
| POST   | `.../dslr/discovery/result`                 | Agent    | Body = `{token, raw_config, body_info}` or `{token, error}`. Server stores raw config (small — JSON of `--list-all-config`), runs `propose_property_map(raw_config, body_info)`, persists `dslr_property_map_proposal`, marks `dslr_discovery.completed_at`, clears `dslr_pending_discovery`. |
| GET    | `.../dslr/discovery/proposal`               | UI       | Returns `{proposal, raw_keys}`. The wizard uses this to populate the confirmation step. |
| PUT    | `.../dslr/property_map`                     | UI       | Replace `dslr_property_map` (post-edit). |
| DELETE | `.../dslr/property_map`                     | UI       | Wipe the map and re-trigger discovery. |

`raw_config` is stored unparsed on the camera record so we can re-run the
heuristics without bothering the camera again — useful when we tweak
`propose_property_map` for a new vendor.

### `propose_property_map`

Pure server-side function, given the parsed key tree:

```python
def propose_property_map(raw_config, body_info) -> DslrPropertyMap:
    # 1. Status tiles: pick the first key in each priority list that exists.
    TELEMETRY_PRIORITY = {
        "battery":          ["batterylevel"],
        "available_shots":  ["availableshots"],
        "shutter_counter":  ["shuttercounter"],
        "exposure_mode":    ["autoexposuremode", "expprogram"],
        "lens_name":        ["lensname"],
        "camera_model":     ["cameramodel", "model"],
    }
    # 2. Setting dropdowns: pick the first key in each priority list.
    SETTING_PRIORITY = {
        "shutterspeed":            ["shutterspeed"],
        "aperture":                ["aperture", "f-number"],
        "iso":                     ["iso", "iso-auto"],
        "exposure_compensation":   ["exposurecompensation"],
        "whitebalance":            ["whitebalance"],
        "image_format":            ["imageformat", "imagequality"],
    }
    # 3. Init keys: same idea for capturetarget / drivemode / focusmode.
```

The function is unit-testable on saved fixtures of `--list-all-config`
output (we'll capture two: Canon R6 and Sony RX100M5A). New vendors are
added by extending the priority tables, not by changing call sites.

## UI flow

`server/app/static/v2/views/camera.js` gets a new state stage in
`renderSettingsTab` when the camera is `gphoto2` *and* has no
`dslr_property_map` yet:

```
┌─ DSLR setup ─────────────────────────────────────────────┐
│  We haven't profiled this camera yet.                    │
│                                                          │
│  When the agent next checks in (~60s) it will run a      │
│  one-time scan of the camera's settings so we know       │
│  which controls to show.                                 │
│                                                          │
│  [ Run discovery ]   ⏳ waiting for agent…               │
└──────────────────────────────────────────────────────────┘
```

`Run discovery` → POST `/dslr/discovery`. The button switches to a
spinner that polls `/dslr/discovery/proposal` every 5s. On result, the
view replaces itself with a confirmation card:

```
┌─ Confirm DSLR layout ────────────────────────────────────┐
│  Detected: Sony DSC-RX100M5A   (247 properties found)    │
│                                                          │
│  Status tiles:                                           │
│    [✓] Camera model   (cameramodel)                      │
│    [✓] Exposure mode  (expprogram)                       │
│    [ ] Battery        — not exposed by this body         │
│    [ ] Available shots— not exposed                      │
│    [ ] Shutter count  — not exposed                      │
│    [ ] Lens name      — not exposed                      │
│                                                          │
│  Capture controls:                                       │
│    [✓] Shutter speed  (shutterspeed)                     │
│    [✓] Aperture       (aperture)                         │
│    [✓] ISO            (iso)                              │
│    [✓] Exposure comp. (exposurecompensation)             │
│    [✓] White balance  (whitebalance)                     │
│    [✓] Image format   (imageformat)                      │
│                                                          │
│  [ Show all 247 keys ]   [ Save layout ]                 │
└──────────────────────────────────────────────────────────┘
```

`Save layout` → PUT `/dslr/property_map`, then `renderDslrSection`
takes over — but reading from `cfg.dslr_property_map.telemetry_tiles` /
`.setting_dropdowns` instead of the hardcoded list. The hardcoded
`DSLR_DEFAULTS` table in `camera.js:1482` becomes a fallback only.

A `[ Re-run discovery ]` button stays on the settings page so the user
can redo the scan after a firmware update or lens swap.

## Acceptance — mapped to the issue

- ✅ "Adding a new gphoto2 body does not require code changes to surface
  basic status" — for any body whose keys appear in our priority tables.
  Bodies with no overlap surface only `cameramodel` (which is
  effectively universal) until a code change adds them.
- ✅ "Sony RX100M5A renders Camera Model + Exposure Program + Capture
  Mode in its status card without showing permanently-blank Canon
  tiles" — the proposal omits unsupported tiles by construction.
- ✅ "Canon R6 still renders existing battery/shots/shutter/lens/exposure
  tiles unchanged" — the priority tables match what `gphoto2_read_telemetry`
  does today, so the proposed map for the R6 is identical.
- ✅ "Mapping is editable post-provision" — Confirm card has checkboxes;
  PUT `/dslr/property_map` replaces the saved map.

## Open questions

1. **Where does the wizard live for *new* cameras?** Today there's no
   per-camera setup wizard for gphoto2 backends — the user just lands
   on the settings tab once the agent reports `active_backend=gphoto2`.
   Simplest answer: surface the "DSLR setup" prompt as a banner at the
   top of the settings tab whenever `active_backend == "gphoto2"` and
   `dslr_property_map` is null. Defer a full multi-step wizard.

2. **Storage size of `raw_config`.** `gphoto2 --list-all-config` on the
   R6 is ~30 KB JSON; on the Sony with its 80-entry ISO list it's
   bigger but still well under 100 KB. Storing it on `camera.config`
   inside `data/config.json` is fine. If it ever bloats we can move it
   to a sidecar file under `data/cameras/{id}/dslr_raw.json`.

3. **What about discovery before the agent is online?** Wizard requires
   a check-in cycle. UI should make that clear ("waiting for agent…")
   and time out after, say, 5 minutes with a helpful error.

4. **Backwards compatibility.** Existing cameras with no
   `dslr_property_map` keep working: the agent falls back to today's
   hardcoded keys whenever `prop_map is None`. No migration needed —
   the user just sees the new "Run discovery" prompt.

## Suggested implementation order

1. Add `DslrPropertyMap` / `DslrDiscoveryStatus` / `DslrPendingDiscovery`
   models in `server/app/main.py`. Wire them into `CameraConfig` and
   `CameraStatus`.
2. Implement `propose_property_map` with unit-test fixtures captured
   from the Canon R6 and Sony RX100M5A.
3. Add the four endpoints. The agent code can stay unchanged for this
   PR — server-side scaffolding is testable on its own.
4. Agent: implement `gphoto2_list_config` and the discovery trigger in
   `run_agent`. Keep the old hardcoded reads as the fallback.
5. Agent: switch telemetry/choices reads to be driven by
   `dslr_property_map` when present.
6. UI: banner + confirmation card in `renderSettingsTab`. Move the
   hardcoded `DSLR_DEFAULTS` and `DSLR_INIT_CHOICES` to be fallbacks
   only.

Each step is independently shippable.
