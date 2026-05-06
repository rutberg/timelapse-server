"""Per-camera DSLR property-map discovery.

This module turns the raw output of `gphoto2 --list-all-config` (as posted by
the agent) into a `DslrPropertyMap` proposal — i.e. the set of telemetry
tiles, capture-setting dropdowns, and init keys to surface in the UI for the
specific body that's attached.

The priority tables below are ordered by vendor specificity: the first
candidate that exists in the raw config wins. This is deliberately a flat
priority list rather than per-vendor branching so adding a new vendor is a
matter of extending the lists, not the dispatch.

Sources for the per-vendor names: libgphoto2's per-camera dumps under
`camlibs/ptp2/cameras/` (canon-eos-r6.txt, nikon-d3400.txt, sony-a7m4.txt,
canon-eos-1000d.txt for `shuttercounter`).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


# (settings_field, label, [(read_key, write_key) candidates], allows_path_search)
SETTING_PRIORITY: List[Tuple[str, str, List[Tuple[str, str]]]] = [
    # Canon uses identical read/write names; Nikon writes through `shutterspeed2`.
    ("shutterspeed", "Shutter speed", [
        ("shutterspeed", "shutterspeed2"),  # Nikon (writable name differs)
        ("shutterspeed", "shutterspeed"),    # Canon, Sony
    ]),
    ("aperture", "Aperture", [
        ("aperture", "aperture"),            # Canon
        ("f-number", "f-number"),            # Nikon, Sony
    ]),
    ("iso", "ISO", [
        ("iso", "iso"),                      # all three
    ]),
    ("exposure_compensation", "Exposure comp.", [
        ("exposurecompensation", "exposurecompensation"),
    ]),
    ("whitebalance", "White balance", [
        ("whitebalance", "whitebalance"),
    ]),
    ("image_format", "Image format", [
        ("imageformat", "imageformat"),      # Canon
        ("imagequality", "imagequality"),    # Nikon, Sony
    ]),
]


INIT_PRIORITY: List[Tuple[str, str, List[Tuple[str, str]]]] = [
    ("capture_target", "Capture target", [
        ("capturetarget", "capturetarget"),
    ]),
    ("drive_mode", "Drive mode", [
        ("drivemode", "drivemode"),          # Canon
        ("capturemode", "capturemode"),      # Nikon, Sony ("Still Capture Mode")
    ]),
    ("focus_mode", "Focus mode", [
        ("focusmode", "focusmode"),          # all three
    ]),
]


# (tile_field, label, kind, [read_key candidates])
TELEMETRY_PRIORITY: List[Tuple[str, str, str, List[str]]] = [
    ("battery", "Battery", "string", ["batterylevel"]),
    ("available_shots", "Available shots", "int", ["availableshots"]),
    ("shutter_counter", "Shutter count", "int", ["shuttercounter"]),
    ("exposure_mode", "Exposure mode", "string", ["autoexposuremode", "expprogram"]),
    ("lens_name", "Lens", "string", ["lensname"]),
    ("camera_model", "Camera", "string", ["cameramodel", "model"]),
]


# Manufacturer-string prefix → canonical vendor name.
VENDOR_FROM_MANUFACTURER: List[Tuple[str, str]] = [
    ("canon", "Canon"),
    ("nikon", "Nikon"),
    ("sony",  "Sony"),
    ("fuji",  "Fuji"),
    ("olympus", "Olympus"),
    ("panasonic", "Panasonic"),
]


def _get_entry(raw_config: Dict[str, Any], key: str) -> Optional[Dict[str, Any]]:
    """Find an entry in the raw tree by short name (e.g. "shutterspeed").

    The agent posts the tree with full paths as keys ("/main/capturesettings/
    shutterspeed"), but the proposer cares only about the leaf name. We
    iterate so the proposer is independent of which `/main/<section>/` the
    body files a property under (Canon puts capturetarget under /settings,
    older bodies put it under /capturesettings, etc.).
    """
    suffix = "/" + key
    for path, entry in raw_config.items():
        if path.endswith(suffix) and isinstance(entry, dict):
            return entry
    return None


def _lookup_value(
    raw_config: Dict[str, Any],
    candidates: List[str],
) -> Optional[str]:
    """First non-empty `current` value among candidate short names."""
    for name in candidates:
        entry = _get_entry(raw_config, name)
        if entry is None:
            continue
        value = entry.get("current")
        if value not in (None, ""):
            return str(value)
    return None


def parse_vendor(manufacturer: Optional[str], model: Optional[str]) -> Optional[str]:
    """Map a free-text manufacturer string to a canonical vendor name.

    Falls back to scanning the model string when manufacturer is missing or
    generic (some Sony bodies report manufacturer="Sony Corporation" while
    older models report nothing distinctive).
    """
    candidates = [s for s in (manufacturer, model) if s]
    for source in candidates:
        lower = source.strip().lower()
        for prefix, canonical in VENDOR_FROM_MANUFACTURER:
            if lower.startswith(prefix) or prefix in lower:
                return canonical
    return None


def parse_body_info(raw_config: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """Pull vendor/model/serial out of /main/status/* entries."""
    manufacturer = _lookup_value(raw_config, ["manufacturer"])
    # On Canon, /main/status/cameramodel and /main/status/model both exist;
    # cameramodel is the marketing name ("Canon EOS R6") which is what we want.
    model = _lookup_value(raw_config, ["cameramodel", "model"])
    serial = _lookup_value(raw_config, ["eosserialnumber", "serialnumber"])
    return {
        "vendor": parse_vendor(manufacturer, model),
        "model": model,
        "serial": serial,
    }


def propose_property_map(
    raw_config: Dict[str, Any],
    discovered_at: str,
    body_info: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a proposal Dict (matching DslrPropertyMap shape) for the body.

    Pure function — no IO. Keeping the return type as a plain dict (rather
    than a Pydantic model) lets the unit tests be model-free, and the
    endpoint validates the dict by passing it through DslrPropertyMap.
    """
    body = body_info or parse_body_info(raw_config)

    telemetry_tiles: List[Dict[str, Any]] = []
    for field, label, kind, candidates in TELEMETRY_PRIORITY:
        for name in candidates:
            entry = _get_entry(raw_config, name)
            if entry is not None:
                telemetry_tiles.append({
                    "field": field,
                    "label": label,
                    "read_key": name,
                    "kind": kind,
                })
                break

    def _matches(read_key: str, write_key: str) -> bool:
        # Read key must exist. When it differs from the write key (Nikon's
        # shutterspeed/shutterspeed2 split), the write key must also exist —
        # otherwise we'd hand the agent a name it can't set on this body.
        if _get_entry(raw_config, read_key) is None:
            return False
        if write_key != read_key and _get_entry(raw_config, write_key) is None:
            return False
        return True

    setting_dropdowns: List[Dict[str, Any]] = []
    for settings_field, label, candidates in SETTING_PRIORITY:
        for read_key, write_key in candidates:
            if _matches(read_key, write_key):
                setting_dropdowns.append({
                    "settings_field": settings_field,
                    "label": label,
                    "read_key": read_key,
                    "write_key": write_key,
                })
                break

    init_keys: List[Dict[str, Any]] = []
    for settings_field, label, candidates in INIT_PRIORITY:
        for read_key, write_key in candidates:
            if _matches(read_key, write_key):
                init_keys.append({
                    "settings_field": settings_field,
                    "label": label,
                    "read_key": read_key,
                    "write_key": write_key,
                })
                break

    return {
        "schema_version": 1,
        "discovered_at": discovered_at,
        "body": body,
        "telemetry_tiles": telemetry_tiles,
        "setting_dropdowns": setting_dropdowns,
        "init_keys": init_keys,
    }


def parse_list_all_config(stdout: str) -> Dict[str, Dict[str, Any]]:
    """Parse `gphoto2 --list-all-config` stdout into a {path: entry} dict.

    The output format is a sequence of blocks, each starting with `END\\n`
    or, for the first block, no separator. Each block has lines like:

        Label: ISO Speed
        Readonly: 0
        Type: RADIO
        Current: 400
        Choice: 0 Auto
        Choice: 1 100
        Choice: 2 200
        END

    Paths are full like `/main/imgsettings/iso`. Entries that don't
    expose a Current value are still recorded (with current=None).
    """
    entries: Dict[str, Dict[str, Any]] = {}
    current_path: Optional[str] = None
    block: Dict[str, Any] = {}
    choices: List[str] = []

    def flush() -> None:
        nonlocal block, choices
        if current_path is not None:
            entry = dict(block)
            entry["choices"] = choices
            entries[current_path] = entry
        block = {}
        choices = []

    for raw in stdout.splitlines():
        line = raw.rstrip()
        if not line:
            continue
        if line == "END":
            flush()
            current_path = None
            continue
        if line.startswith("/"):
            # Start of a new block.
            flush()
            current_path = line.strip()
            continue
        if current_path is None:
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if key == "Label":
            block["label"] = value
        elif key == "Type":
            block["type"] = value
        elif key == "Readonly":
            block["readonly"] = value not in ("0", "false", "False", "")
        elif key == "Current":
            block["current"] = value
        elif key == "Choice":
            # "Choice: 0 Auto" → "Auto"
            parts = value.split(None, 1)
            if len(parts) == 2:
                choices.append(parts[1])
    # Trailing block without END (some gphoto2 versions omit the final END).
    flush()
    return entries
