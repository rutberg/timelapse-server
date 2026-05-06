# Implementation plan — Per-camera DSLR/PTP property discovery

Tracking issue: [#13 — Per-camera DSLR/PTP property discovery wizard](https://github.com/rutberg/timelapse/issues/13)

Scope: explicit support for **Canon, Nikon, and Sony DSLRs minimum** (per
follow-up direction on the issue), with a property-map architecture that
generalizes to other PTP vendors without code changes.

Status: **plan**, not yet implemented. Goal is to lock the data model, the
agent ↔ server protocol, the vendor priority tables, and the UI before any
code lands.

## Problem (from #13)

Today the agent and the camera-settings UI both hardcode a Canon-flavoured
set of `gphoto2` PTP properties:

- `agent/timelapse_agent.py:848` (`gphoto2_read_telemetry`) reads
  `batterylevel`, `availableshots`, `shuttercounter`, `lensname`,
  `cameramodel`, with one fallback (`autoexposuremode → expprogram`).
- `agent/timelapse_agent.py:741` (`_DSLR_CHOICE_KEYS`) lists six
  `capturesettings`/`imgsettings` keys that the agent reads choices for.
- `server/app/static/v2/views/camera.js:1676` (`renderDslrSection`) renders
  fixed Battery / Available shots / Shutter count tiles plus six fixed
  capture-setting dropdowns.

Sony exposes a different subset (no battery / shots / shutter counter /
lens name; uses `expprogram` instead of `autoexposuremode`); Nikon is
different again — and worse, **Nikon's writable shutter-speed key is
`shutterspeed2`, not `shutterspeed`**, so a single-name lookup fails.

We need a one-time **discovery step** per camera that asks the body what
it exposes, stores a per-body **property map** on the camera record, and
then drives both telemetry reads and the settings UI from that map
instead of hardcoded keys.

## Vendor research (libgphoto2 dumps)

The plan below is grounded in the official per-camera dumps in
[`gphoto/libgphoto2`](https://github.com/gphoto/libgphoto2/tree/master/camlibs/ptp2/cameras).
Specifically:

- Canon: `canon-eos-r6.txt`, `canon-eos-1000d.txt` (older body for
  `shuttercounter` coverage)
- Nikon: `nikon-d3400.txt`, `nikon-d850.txt`
- Sony: `sony-a7m4.txt` (Sony RX100M5A — what the issue describes —
  exposes a near-identical subset)

### Vendor differences cheat-sheet

| Concept | Canon | Nikon | Sony |
|---|---|---|---|
| Aperture | `aperture` | **`f-number`** | **`f-number`** |
| Image format | `imageformat` | **`imagequality`** | **`imagequality`** |
| Exposure mode | `autoexposuremode` | **`expprogram`** | **`expprogram`** |
| Drive mode | `drivemode` | **`capturemode`** | **`capturemode`** |
| Shutter speed (read) | `shutterspeed` | `shutterspeed` | `shutterspeed` |
| Shutter speed (**write**) | `shutterspeed` | **`shutterspeed2`** ⚠️ | `shutterspeed` |
| ISO | `iso` | `iso` | `iso` |
| Exposure compensation | `exposurecompensation` | `exposurecompensation` | `exposurecompensation` |
| White balance | `whitebalance` | `whitebalance` | `whitebalance` |
| Capture target | `capturetarget` | `capturetarget` | `capturetarget` (newer bodies only) |
| Battery level | `batterylevel` | `batterylevel` | **not exposed** (only PTP `0x5001`) |
| Available shots | `availableshots` | **not exposed** | **not exposed** |
| Shutter counter | `shuttercounter` (older bodies) | not exposed | not exposed |
| Lens name | `lensname` | **not exposed** (only `focallength`) | **not exposed** |
| Camera model | `cameramodel` + `model` | `cameramodel` | `cameramodel` |
| Manufacturer | `manufacturer` | `manufacturer` | `manufacturer` |
| Serial | `eosserialnumber` + `serialnumber` | `serialnumber` | `serialnumber` |
| Focus mode | `focusmode` | `focusmode` + `focusmode2` | `focusmode` |

The two non-obvious gotchas:

1. **Nikon writes through `shutterspeed2`.** `shutterspeed` is read-only on
   Nikon bodies; trying to set it fails. `shutterspeed2` is the writable
   RADIO equivalent. The property map therefore needs separate
   `read_key`/`write_key` slots for each setting.
2. **Sony hides battery behind raw PTP code.** `batterylevel` doesn't appear
   under `/main/status/`; only `/main/other/5001` does, and it returns
   menu indexes (0–35), not "75%". For a v1 we just don't render the
   battery tile on Sony. (See "Out of scope".)

### Out of scope (still, per the issue)

- Cross-vendor *value* normalisation (e.g. translating Sony `"32/10"` to
  Canon-style `"3.2"`, or Nikon `"1/30s"` vs Canon `"1/30"`).
- Decoding raw `/main/other/<hex>` properties (e.g. Sony battery via
  `0x5001`). v1 only uses `/main/status`, `/main/imgsettings`,
  `/main/capturesettings`, `/main/settings` paths.
- `rpicam` discovery.

## Architecture at a glance

```
┌────────────────┐  1. POST /dslr/discovery            ┌──────────────────┐
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
│  4. gphoto2 --list-all-config → POST /dslr/discovery/result   │
│ ─────────────────────────────────────────────────▶            │
└────────────────┘                                              │
                                                                │ runs
                                                                │ propose_property_map
                                                                ▼
                                                       dslr_property_map_proposal
                                                       (camera record)

┌────────────────┐  5. GET /dslr/discovery/proposal    ┌──────────────────┐
│ Camera UI      │ ─────────────────────────────────▶  │ Server returns   │
│ wizard         │  show proposed status tiles +       │ proposal +       │
│                │  setting dropdowns, user edits ─▶   │ raw key list     │
│                │  PUT /dslr/property_map             │                  │
└────────────────┘                                     └──────────────────┘
```

Steps 1–4 = discovery. Step 5 = user confirmation. The result is
`camera.config.dslr_property_map`, which the agent and `renderDslrSection`
both read going forward.

## Data model

### New: `camera.config.dslr_property_map`

```python
class DslrTelemetryTile(BaseModel):
    label: str                      # "Battery", "Available shots", ...
    read_key: str                   # "batterylevel"
    kind: Literal["string", "int", "percent"]   # render hint

class DslrSettingDropdown(BaseModel):
    label: str                      # "Shutter speed"
    settings_field: str             # snake_case key on DslrSettings
                                    # ("shutterspeed", "exposure_compensation", …)
    read_key: str                   # "shutterspeed"
    write_key: str                  # "shutterspeed2" on Nikon, same as read_key elsewhere

class DslrInitKey(BaseModel):
    settings_field: str             # "capture_target", "drive_mode", "focus_mode"
    read_key: str
    write_key: str

class DslrBodyInfo(BaseModel):
    vendor: Optional[str]           # "Canon", "Nikon", "Sony" — parsed from
                                    # /main/status/manufacturer
    model: Optional[str]            # "Canon EOS R6"
    serial: Optional[str]           # eosserialnumber → serialnumber

class DslrPropertyMap(BaseModel):
    schema_version: int = 1
    discovered_at: str              # ISO timestamp from agent
    body: DslrBodyInfo
    telemetry_tiles: List[DslrTelemetryTile]
    setting_dropdowns: List[DslrSettingDropdown]
    init_keys: List[DslrInitKey]
```

`read_key`/`write_key` separation is mandatory because of Nikon's
`shutterspeed`/`shutterspeed2` quirk. On Canon and Sony they're the same
string; on Nikon, the proposer fills them in differently.

### `DslrSettings` stays as-is

The existing `DslrSettings` model (`shutterspeed`, `aperture`, `iso`,
`exposure_compensation`, `whitebalance`, `image_format`, `capture_target`,
`drive_mode`, `focus_mode`, `reinit_token`) stays the canonical save shape.
It is the *union* of per-vendor concepts in snake_case; the property map
is what binds each `settings_field` to the right gphoto2 key on this body.
Sony bodies that don't expose `capture_target` simply get no init-key
entry for it, and the saved value stays `None`.

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
    token: str
    requested_at: str
```

Cleared by the server once the matching `/discovery/result` lands.

## `propose_property_map` — vendor priority tables

Pure server-side function. Given the agent-supplied raw config tree and
body info, picks the first key in each priority list that exists on the
body. The lists are **ordered by vendor specificity**, not by alphabet:
when a vendor uses a unique name (Canon's `aperture`, Nikon's `f-number`),
that name comes first so the proposer doesn't surface a misleading match.

```python
# Each entry: settings_field → list of (read_key, write_key) candidates,
# tried in order until one is present in the raw config tree.

SETTING_PRIORITY: Dict[str, List[Tuple[str, str]]] = {
    "shutterspeed":          [("shutterspeed",   "shutterspeed"),    # Canon, Sony
                              ("shutterspeed",   "shutterspeed2")],  # Nikon
    "aperture":              [("aperture",       "aperture"),        # Canon
                              ("f-number",       "f-number")],       # Nikon, Sony
    "iso":                   [("iso",            "iso")],            # all
    "exposure_compensation": [("exposurecompensation", "exposurecompensation")],
    "whitebalance":          [("whitebalance",   "whitebalance")],
    "image_format":          [("imageformat",    "imageformat"),     # Canon
                              ("imagequality",   "imagequality")],   # Nikon, Sony
}

INIT_PRIORITY: Dict[str, List[Tuple[str, str]]] = {
    "capture_target":        [("capturetarget", "capturetarget")],
    "drive_mode":            [("drivemode",     "drivemode"),        # Canon
                              ("capturemode",   "capturemode")],     # Nikon, Sony
    "focus_mode":            [("focusmode",     "focusmode")],       # all
}

# Telemetry uses single read keys (no write side).
TELEMETRY_PRIORITY: Dict[str, Tuple[str, List[str], str]] = {
    # tile_field: (label, read_key candidates, kind)
    "battery":         ("Battery",         ["batterylevel"],                  "string"),
    "available_shots": ("Available shots", ["availableshots"],                "int"),
    "shutter_counter": ("Shutter count",   ["shuttercounter"],                "int"),
    "exposure_mode":   ("Exposure mode",   ["autoexposuremode", "expprogram"], "string"),
    "lens_name":       ("Lens",            ["lensname"],                      "string"),
    "camera_model":    ("Camera",          ["cameramodel", "model"],          "string"),
}

VENDOR_FROM_MANUFACTURER = {
    # case-insensitive prefix match
    "canon":  "Canon",
    "nikon":  "Nikon",
    "sony":   "Sony",
}
```

The proposer returns a `DslrPropertyMap` with:

- `body.vendor` parsed from `/main/status/manufacturer` (fall back to
  parsing `cameramodel`).
- `body.model` from `cameramodel`, `body.serial` from
  `eosserialnumber → serialnumber`.
- `telemetry_tiles` for each `TELEMETRY_PRIORITY` entry whose first
  matching read key exists. Sony omits Battery/Available shots/Shutter
  count/Lens entirely → tiles for those just don't appear.
- `setting_dropdowns` from `SETTING_PRIORITY`. Each picks the first
  `(read, write)` pair whose `read_key` is present.
- `init_keys` from `INIT_PRIORITY`. Same rule.

### Expected outputs (sanity-check)

Running `propose_property_map` against each of the three reference dumps
should produce:

**Canon EOS R6:**
- tiles: Battery, Available shots, Lens, Camera, Exposure mode (no
  Shutter count — R6 doesn't expose it; older Canons like 1000D do)
- dropdowns: shutter (rw=shutterspeed), aperture (rw=aperture), iso, exp.
  comp., wb, image_format (rw=imageformat)
- init: capture_target, drive_mode (drivemode), focus_mode

**Nikon D3400/D850:**
- tiles: Battery, Camera, Exposure mode (no Available shots / Shutter
  count / Lens — Nikon doesn't expose them under `/main/status/`)
- dropdowns: shutter (r=shutterspeed, **w=shutterspeed2**), aperture (rw=
  f-number), iso, exp. comp., wb, image_format (rw=imagequality)
- init: capture_target, drive_mode (capturemode), focus_mode

**Sony A7 IV / RX100M5A:**
- tiles: Camera, Exposure mode (no Battery, Available shots, Shutter
  count, Lens)
- dropdowns: shutter (rw=shutterspeed), aperture (rw=f-number), iso, exp.
  comp., wb, image_format (rw=imagequality)
- init: capture_target (only newer bodies), drive_mode (capturemode),
  focus_mode

These three outputs are the unit-test fixtures.

## Server-side endpoints

All scoped under the existing `/api/cameras/{camera_id}/...` namespace.

| Method | Path                                  | Caller | Purpose |
|--------|---------------------------------------|--------|---------|
| POST   | `.../dslr/discovery`                  | UI     | Mint discovery token; set `dslr_pending_discovery` on the config so the agent picks it up on next poll. Returns `{token}`. |
| POST   | `.../dslr/discovery/result`           | Agent  | Body = `{token, raw_config, body_info}` or `{token, error}`. Server stores raw config (small — JSON of `--list-all-config`), runs `propose_property_map`, persists the proposal, marks `dslr_discovery.completed_at`, clears `dslr_pending_discovery`. |
| GET    | `.../dslr/discovery/proposal`         | UI     | Returns `{proposal, raw_keys}`. Wizard uses this to populate the confirmation step. |
| PUT    | `.../dslr/property_map`               | UI     | Replace `dslr_property_map` (post-edit). |
| DELETE | `.../dslr/property_map`               | UI     | Wipe the map and re-trigger discovery. |

`raw_config` is stored unparsed on the camera record so we can re-run the
proposer (e.g. when we add Fuji support) without bothering the camera again.

## Agent-side flow

New helper in `agent/timelapse_agent.py`:

```python
def gphoto2_list_config() -> Dict[str, Dict[str, Any]]:
    """Run `gphoto2 --list-all-config`; return {path: {label, type, choices,
    current, readonly}} for every PTP property the camera exposes.

    Path = "/main/status/batterylevel". Type = TEXT|RADIO|MENU|RANGE|TOGGLE.
    """
```

Discovery trigger lives inside the existing config-poll loop
(`run_agent`, `agent/timelapse_agent.py:1162`):

```python
if state.active_backend == "gphoto2":
    pending = remote_config.get("dslr_pending_discovery")
    if pending and pending["token"] != state.last_discovery_token:
        try:
            tree = gphoto2_list_config()
            body = parse_body_info(tree)        # vendor / model / serial
            post_discovery_result(settings, pending["token"], tree, body)
            state.last_discovery_token = pending["token"]
        except Exception as e:
            post_discovery_result(settings, pending["token"], error=str(e))
```

Once `dslr_property_map` is set on the camera config, the existing
telemetry/choices reads become driven by the map:

```python
def gphoto2_read_telemetry(prop_map: DslrPropertyMap) -> Dict[str, Any]:
    return {
        tile.settings_field if hasattr(tile, "settings_field") else label_key(tile.label):
            _coerce(_gphoto2_get_current(tile.read_key), tile.kind)
        for tile in prop_map.telemetry_tiles
    }

def gphoto2_read_choices_and_current(prop_map: DslrPropertyMap):
    keys = ([d.read_key for d in prop_map.setting_dropdowns]
            + [k.read_key for k in prop_map.init_keys])
    # … existing loop, but driven by the map.

def gphoto2_apply_sequence_settings(dslr, prop_map):
    for d in prop_map.setting_dropdowns:
        value = dslr.get(d.settings_field)
        if value is not None:
            subprocess.run(["gphoto2", "--set-config",
                            f"{d.write_key}={value}"], check=True, ...)
```

Backwards compat: when `prop_map is None` (existing cameras pre-discovery),
the agent falls back to today's hardcoded keys. No migration needed; the
user just sees the new "Run discovery" prompt on their settings tab.

The hardcoded `_DSLR_CHOICE_KEYS` and the Canon-flavored fallback chain in
`_gphoto2_first_current` are removed once a body has a property map.

## UI flow

`server/app/static/v2/views/camera.js` `renderSettingsTab` gets a new
state stage when the camera is `gphoto2` *and* has no `dslr_property_map`
yet — surfaced as a banner card at the top of the settings tab:

```
┌─ DSLR setup needed ──────────────────────────────────────┐
│  We haven't profiled this camera yet.                    │
│  When the agent next checks in (~60s) it will run a      │
│  one-time scan of the camera's properties so we know     │
│  which controls to show.                                 │
│                                                          │
│  [ Run discovery ]   ⏳ waiting for agent…               │
└──────────────────────────────────────────────────────────┘
```

`Run discovery` → POST `/dslr/discovery`. The button switches to a
spinner that polls `/dslr/discovery/proposal` every 5s. On result, the
banner replaces itself with a confirmation card:

```
┌─ Confirm DSLR layout ────────────────────────────────────┐
│  Detected: Sony ILCE-7M4   (138 properties found)        │
│  Vendor: Sony                                            │
│                                                          │
│  Status tiles:                                           │
│    [✓] Camera             (cameramodel)                  │
│    [✓] Exposure mode      (expprogram)                   │
│    [ ] Battery            — not exposed by this body     │
│    [ ] Available shots    — not exposed                  │
│    [ ] Shutter count      — not exposed                  │
│    [ ] Lens               — not exposed                  │
│                                                          │
│  Capture controls:                                       │
│    [✓] Shutter speed     (read=shutterspeed,             │
│                           write=shutterspeed)            │
│    [✓] Aperture          (read=f-number, write=f-number) │
│    [✓] ISO               (iso)                           │
│    [✓] Exposure comp.    (exposurecompensation)          │
│    [✓] White balance     (whitebalance)                  │
│    [✓] Image format      (imagequality)                  │
│                                                          │
│  [ Show all 138 keys ]   [ Save layout ]                 │
└──────────────────────────────────────────────────────────┘
```

The Nikon variant of this card is identical except it shows
`read=shutterspeed, write=shutterspeed2` and `read=f-number,
write=f-number`. The mismatch is the user's signal that the proposer
correctly handled Nikon's writable-shutter quirk.

`Save layout` → PUT `/dslr/property_map`. From then on, `renderDslrSection`
reads `cfg.dslr_property_map.telemetry_tiles` / `.setting_dropdowns`
instead of the hardcoded list. The hardcoded `DSLR_DEFAULTS` and
`DSLR_INIT_CHOICES` tables (`camera.js:1482`, `camera.js:1641`) become
fallbacks only — used when the map is missing.

A `[ Re-run discovery ]` button stays on the settings page so the user
can redo the scan after a firmware update or lens swap.

## Acceptance — mapped to the issue

- ✅ "Adding a new gphoto2 body does not require code changes to surface
  basic status" — for any body whose keys appear in our priority tables,
  i.e. Canon, Nikon, Sony, plus any vendor that re-uses those names.
  Other vendors (Fuji, Olympus) get the universal subset (`cameramodel`,
  `iso`, `whitebalance`) automatically; their unique keys can be added by
  extending `SETTING_PRIORITY` without touching call sites.
- ✅ "Sony renders Camera Model + Exposure Program + Capture Mode in its
  status card without permanently-blank Canon tiles" — the proposal
  omits Battery / Available shots / Shutter count / Lens by construction.
- ✅ "Canon R6 still renders existing battery/shots/lens/exposure tiles
  unchanged" — the priority tables match what `gphoto2_read_telemetry`
  does today, so the proposed map for the R6 is identical (plus an
  explicit Camera tile).
- ✅ "Nikon supported alongside Canon and Sony" — Nikon's
  `f-number`, `imagequality`, `expprogram`, `capturemode`, and
  `shutterspeed2` write-key quirk are all handled by the priority tables.
- ✅ "Mapping is editable post-provision" — confirm card has checkboxes;
  PUT `/dslr/property_map` replaces the saved map. DELETE re-triggers
  discovery.

## Open questions

1. **Where does the wizard live for *new* cameras?** Today there's no
   per-camera setup wizard for gphoto2 — the user just lands on the
   settings tab once the agent reports `active_backend=gphoto2`.
   Recommendation: surface the "DSLR setup" prompt as a banner at the
   top of the settings tab whenever `active_backend == "gphoto2"` and
   `dslr_property_map` is null. Defer a full multi-step wizard.

2. **Storage size of `raw_config`.** A full `gphoto2 --list-all-config`
   on the R6 is ~30 KB JSON; on a Sony with its 80-entry ISO list it's
   bigger but well under 100 KB. Storing it on `camera.config` inside
   `data/config.json` is fine. If it ever bloats we can move to a sidecar
   `data/cameras/{id}/dslr_raw.json`.

3. **What about discovery before the agent is online?** Wizard requires
   a check-in cycle. UI should show "waiting for agent…" and time out
   after ~5 minutes with a helpful error.

4. **Backwards compatibility.** Existing cameras with no
   `dslr_property_map` keep working: the agent falls back to today's
   hardcoded keys whenever `prop_map is None`. No migration needed.

5. **Sony battery (`/main/other/5001`).** Out of scope for v1, but worth
   noting that Sony does expose battery — just under a hex code that
   returns menu indexes, not "75%". A v2 can add a `raw_ptp_lookup` map
   with hand-curated value tables.

## Implementation plan — six steps, each independently shippable

### Step 1 — Server: data model + endpoint scaffolding

**Files:** `server/app/main.py`

- Add `DslrTelemetryTile`, `DslrSettingDropdown`, `DslrInitKey`,
  `DslrBodyInfo`, `DslrPropertyMap`, `DslrDiscoveryStatus`,
  `DslrPendingDiscovery` Pydantic models.
- Extend `CameraConfig` with `dslr_property_map: Optional[DslrPropertyMap]`
  and `dslr_pending_discovery: Optional[DslrPendingDiscovery]`.
- Extend `CameraStatus` with `dslr_discovery: Optional[DslrDiscoveryStatus]`
  and `dslr_raw_config: Optional[Dict[str, Dict[str, Any]]]`.
- Add the five endpoints (`POST /dslr/discovery`,
  `POST /dslr/discovery/result`, `GET /dslr/discovery/proposal`,
  `PUT /dslr/property_map`, `DELETE /dslr/property_map`).
- All endpoints reject when `active_backend != "gphoto2"`.

Tests: HTTP-level tests round-tripping each endpoint.

### Step 2 — Server: `propose_property_map` + fixtures

**Files:** `server/app/main.py` (or new `server/app/dslr_discovery.py`),
`tests/server/test_dslr_proposer.py`,
`tests/server/fixtures/dslr/{canon-eos-r6,nikon-d3400,sony-a7m4}.json`.

- Implement the priority tables exactly as defined above.
- Implement `propose_property_map(raw_config: Dict[path, entry], body_info)
  -> DslrPropertyMap`.
- Capture three fixture files by parsing the libgphoto2 dumps in
  `camlibs/ptp2/cameras/` into the same `{path: entry}` shape the agent
  will produce. (The dumps are already in this format — minimal massaging.)
- Unit tests assert the expected proposal for each fixture per the
  "Expected outputs" section.
- Add a fourth fixture for an *unknown* vendor (e.g. an Olympus dump) and
  assert it produces a sane minimal map (Camera + ISO + WhiteBalance only)
  rather than crashing.

### Step 3 — Agent: discovery handshake

**Files:** `agent/timelapse_agent.py`, `tests/agent/test_discovery.py`,
bump `agent/VERSION`.

- Add `gphoto2_list_config()` parsing `gphoto2 --list-all-config`.
- Add `parse_body_info(tree) -> DslrBodyInfo` (vendor / model / serial).
- Add `post_discovery_result(settings, token, tree=None, body=None,
  error=None)`.
- In `run_agent`'s config-poll branch, check
  `remote_config["dslr_pending_discovery"]` and run discovery once per
  token. Stash `last_discovery_token` on `AgentState`.
- Existing `gphoto2_read_telemetry` and `gphoto2_read_choices_and_current`
  stay unchanged for now — no behaviour change yet.

Tests: snapshot of `gphoto2 --list-all-config` parsed correctly; mock
HTTP for `post_discovery_result`.

### Step 4 — Agent: switch reads/writes to property map

**Files:** `agent/timelapse_agent.py`.

- Refactor `gphoto2_read_telemetry`, `gphoto2_read_choices_and_current`,
  `gphoto2_apply_sequence_settings`, `gphoto2_apply_init_settings` to
  take an optional `prop_map: Optional[DslrPropertyMap]`.
- When `prop_map is None`, fall back to today's hardcoded keys.
- When `prop_map` is set, drive everything from it — including the
  Nikon `read_key` vs `write_key` split.
- Drop `_DSLR_CHOICE_KEYS` and `_gphoto2_first_current`'s static fallback
  chain when a map is present.

Tests: parametrised tests across the three vendor fixtures.

### Step 5 — UI: discovery banner + confirmation card

**Files:** `server/app/static/v2/views/camera.js`.

- Add a `renderDiscoveryBanner(camera)` block at the top of
  `renderSettingsTab` shown when `active_backend == "gphoto2"` and
  `cfg.dslr_property_map` is null.
- "Run discovery" button → POST `/dslr/discovery`, then poll
  `GET /dslr/discovery/proposal` on a 5s timer until populated or 5min
  timeout.
- `renderDiscoveryConfirmCard(proposal)` with checkboxes for each
  proposed tile + dropdown, plus a collapsible "Show all N keys" panel
  that lists the raw `--list-all-config` paths.
- "Save layout" → PUT `/dslr/property_map`. Reload settings tab.
- Add a "Re-run discovery" link inside the existing DSLR Status card so
  it's reachable post-confirmation.

### Step 6 — UI: `renderDslrSection` reads from property map

**Files:** `server/app/static/v2/views/camera.js`.

- Replace the hardcoded telemetry tiles in `renderDslrSection` with a
  loop over `cfg.dslr_property_map.telemetry_tiles`.
- Replace the six hardcoded `dslrSelect` calls with a loop over
  `cfg.dslr_property_map.setting_dropdowns`.
- `DSLR_DEFAULTS` and `DSLR_INIT_CHOICES` become fallbacks used only
  when the map is missing (legacy / pre-discovery cameras).
- `buildDslrPayload` becomes a loop that pulls each `settings_field`
  from its corresponding `<select>` element by id.

Each step compiles and ships behind the existing `active_backend ==
"gphoto2"` gate. Steps 1–2 are server-only. Step 3 ships an agent that's
backwards compatible. Steps 4–6 sequentially turn on the new behaviour.

## References

- Issue #13: <https://github.com/rutberg/timelapse/issues/13>
- libgphoto2 per-camera dumps:
  <https://github.com/gphoto/libgphoto2/tree/master/camlibs/ptp2/cameras>
  - `canon-eos-r6.txt`, `canon-eos-1000d.txt`
  - `nikon-d3400.txt`, `nikon-d850.txt`
  - `sony-a7m4.txt`, `sony-ilce-7m3.txt`
- gphoto2 remote control reference: <http://gphoto.github.io/doc/remote/>
- Nikon `shutterspeed2` write-key bug:
  <https://github.com/zaeleus/ffi-gphoto2/issues/3>
- Sony battery via raw PTP `0x5001`: noted in
  `gphoto2 --list-all-config` output for ILCE-7 series under
  `/main/other/5001`.
