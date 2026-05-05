#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import mimetypes
import os
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def _read_agent_version() -> str:
    here = Path(__file__).resolve().parent
    candidates = [
        here / "VERSION",
        here.parent / "VERSION",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.read_text(encoding="utf-8").strip()
    return "0.0.0-dev"


AGENT_VERSION = _read_agent_version()


class UpdateError(RuntimeError):
    pass


DEFAULT_REMOTE_CONFIG = {
    "enabled": True,
    "interval_seconds": 900,
    "image_width": None,
    "image_height": None,
    "jpeg_quality": 85,
    "config_version": 0,
}


@dataclass
class AgentState:
    last_capture_at: Optional[str] = None
    last_upload_at: Optional[str] = None
    last_error: Optional[str] = None
    pending_count: int = 0
    pending_bytes: int = 0
    in_schedule: bool = True
    local_hour: int = 0
    current_light: Optional[int] = None


def next_allowed_hour(current_hour: int, capture_hours: list) -> int:
    """Smallest hour in capture_hours strictly greater than current_hour, wrapping at 24."""
    sorted_hours = sorted(set(capture_hours))
    for hour in sorted_hours:
        if hour > current_hour:
            return hour
    return sorted_hours[0]


FALLBACK_MAX_PENDING_BYTES = 500_000_000  # used when disk_usage fails


def auto_max_pending_bytes(work_dir: Path) -> int:
    """Return half of work_dir's filesystem total capacity, in bytes."""
    try:
        usage = shutil.disk_usage(work_dir)
    except OSError:
        return FALLBACK_MAX_PENDING_BYTES
    return max(usage.total // 2, FALLBACK_MAX_PENDING_BYTES)


def resolve_max_pending_bytes(settings: Dict[str, Any], work_dir: Path) -> int:
    """Pick the pending cap to use.

    - If max_pending_bytes is missing or null in config: auto-detect (50% of
      work_dir partition).
    - 0 means unlimited (no eviction).
    - Any other positive int is honored verbatim.
    """
    configured = settings.get("max_pending_bytes")
    if configured is None:
        return auto_max_pending_bytes(work_dir)
    return int(configured)


def solar_window(
    today: date,
    latitude: float,
    longitude: float,
    utc_offset_hours: float = 0.0,
) -> Optional[Tuple[int, int]]:
    """Return (sunrise_hour, sunset_hour_exclusive) in local time, or None.

    Uses the NOAA solar position approximation. Resolution is hour-rounded —
    enough for capture-window scheduling. Returns:
        (0, 24) when the sun never sets (polar day),
        None    when the sun never rises (polar night).

    sunset_hour_exclusive uses ceil(), so a sunset at 22:08 yields 23 —
    meaning hours 0..22 are included, which correctly covers the 22:xx window.
    """
    n = today.timetuple().tm_yday
    gamma = 2 * math.pi / 365 * (n - 1)

    # Equation of time (minutes)
    eqtime = 229.18 * (
        0.000075
        + 0.001868 * math.cos(gamma)
        - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2 * gamma)
        - 0.040849 * math.sin(2 * gamma)
    )

    # Solar declination (radians)
    decl = (
        0.006918
        - 0.399912 * math.cos(gamma)
        + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2 * gamma)
        + 0.000907 * math.sin(2 * gamma)
        - 0.002697 * math.cos(3 * gamma)
        + 0.00148 * math.sin(3 * gamma)
    )

    lat_rad = math.radians(latitude)
    # Hour angle at sunrise/sunset (zenith = 90.833° to include refraction).
    cos_ha = (math.cos(math.radians(90.833)) - math.sin(lat_rad) * math.sin(decl)) / (
        math.cos(lat_rad) * math.cos(decl)
    )
    if cos_ha < -1.0:
        return (0, 24)         # polar day
    if cos_ha > 1.0:
        return None            # polar night
    ha = math.degrees(math.acos(cos_ha))

    # Solar noon (UTC minutes)
    solar_noon_utc_min = 720 - 4 * longitude - eqtime
    sunrise_utc_min = solar_noon_utc_min - 4 * ha
    sunset_utc_min = solar_noon_utc_min + 4 * ha

    sunrise_local_h = (sunrise_utc_min / 60.0) + utc_offset_hours
    sunset_local_h = (sunset_utc_min / 60.0) + utc_offset_hours

    sunrise_h = max(0, min(23, int(math.floor(sunrise_local_h))))
    sunset_h = max(1, min(24, int(math.ceil(sunset_local_h))))
    return (sunrise_h, sunset_h)


def daylight_capture_hours(
    today: date,
    latitude: Optional[float],
    longitude: Optional[float],
    utc_offset_hours: float = 0.0,
) -> List[int]:
    """Hours of day to capture in 'daylight' mode. Falls back to 06:00–20:00."""
    if latitude is None or longitude is None:
        return list(range(6, 20))
    window = solar_window(today, latitude, longitude, utc_offset_hours)
    if window is None:
        return []
    sunrise_h, sunset_h = window
    return list(range(sunrise_h, sunset_h))


def hour_in_schedule(now: datetime, capture_hours: Optional[list]) -> bool:
    """Return True if `now`'s hour is within the schedule (or no schedule).

    capture_hours of None means "no schedule, always allowed". An empty list
    would mean "never allowed" — but the server validator rejects it, so we
    treat it as "always" too for defensive behavior.
    """
    if not capture_hours:
        return True
    return now.hour in set(capture_hours)


def is_in_schedule(
    now: datetime,
    capture_hours: Optional[list],
    schedule_days: Optional[list] = None,
) -> bool:
    """Combined gate: hour-of-day AND ISO-weekday must both allow capture.

    capture_hours: None = no hour restriction; list of 0-23 ints otherwise.
    schedule_days: None = every day; list of 1-7 ISO weekdays otherwise.
                   Empty list (length zero, not None) means "paused — no day enabled".
    """
    if schedule_days is not None:
        if now.isoweekday() not in set(schedule_days):
            return False
    return hour_in_schedule(now, capture_hours)


def measure_pending(work_dir: Path) -> tuple[int, int]:
    pending_dir = work_dir / "pending"
    if not pending_dir.exists():
        return (0, 0)
    count = 0
    total = 0
    for entry in pending_dir.glob("*.jpg"):
        try:
            total += entry.stat().st_size
        except OSError:
            continue
        count += 1
    return (count, total)


def evict_pending(work_dir: Path, max_bytes: int) -> tuple[int, int]:
    """Delete oldest pending captures until total size <= max_bytes.

    Returns (evicted_count, evicted_bytes). max_bytes <= 0 means no cap
    (returns 0, 0). Sidecar .json metadata is removed alongside its image.
    """
    if max_bytes <= 0:
        return (0, 0)
    pending_dir = work_dir / "pending"
    if not pending_dir.exists():
        return (0, 0)
    files = sorted(pending_dir.glob("*.jpg"))
    sizes = []
    total = 0
    for path in files:
        try:
            size = path.stat().st_size
        except OSError:
            sizes.append(0)
            continue
        sizes.append(size)
        total += size
    if total <= max_bytes:
        return (0, 0)
    evicted_count = 0
    evicted_bytes = 0
    for path, size in zip(files, sizes):
        if total <= max_bytes:
            break
        try:
            path.unlink()
        except OSError:
            continue
        path.with_suffix(".json").unlink(missing_ok=True)
        total -= size
        evicted_count += 1
        evicted_bytes += size
    return (evicted_count, evicted_bytes)


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as config_file:
        return json.load(config_file)


def write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as config_file:
        json.dump(data, config_file, indent=2, sort_keys=True)
        config_file.write("\n")
    temp_path.replace(path)


def request_json(url: str, timeout: int = 20) -> Dict[str, Any]:
    request = Request(url, method="GET")
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def post_json(url: str, payload: Dict[str, Any], timeout: int = 15) -> Dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def download_bundle(url: str, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / Path(url).name
    request = Request(url, method="GET")
    with urlopen(request, timeout=120) as response:
        target.write_bytes(response.read())
    return target


def verify_sha256(path: Path, expected_hex: str) -> None:
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual.lower() != expected_hex.lower():
        raise UpdateError(f"sha256 mismatch: expected {expected_hex}, got {actual}")


def _is_macos_metadata(name: str) -> bool:
    # AppleDouble (._foo) and Spotlight noise (.DS_Store, __MACOSX/) end up in
    # tar archives created on macOS. Skip them on extract so install_bundle's
    # "exactly one top-level directory" invariant holds.
    base = Path(name).name
    if base.startswith("._") or base == ".DS_Store":
        return True
    return name.startswith("__MACOSX/") or name == "__MACOSX"


def _safe_extract(tar: tarfile.TarFile, dest: Path) -> None:
    dest_resolved = dest.resolve()
    members = []
    for member in tar.getmembers():
        if _is_macos_metadata(member.name):
            continue
        member_path = (dest / member.name).resolve()
        try:
            member_path.relative_to(dest_resolved)
        except ValueError as error:
            raise UpdateError(f"unsafe path in bundle: {member.name}") from error
        if member.issym() or member.islnk():
            raise UpdateError(f"unsafe symlink in bundle: {member.name}")
        members.append(member)
    tar.extractall(dest, members=members)


def install_bundle(bundle_path: Path, version: str, install_root: Path) -> Path:
    install_root.mkdir(parents=True, exist_ok=True)
    staging = install_root / f".{version}.staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()
    try:
        with tarfile.open(bundle_path, "r:gz") as tar:
            _safe_extract(tar, staging)
        extracted = list(staging.iterdir())
        if len(extracted) != 1 or not extracted[0].is_dir():
            raise UpdateError("bundle must contain exactly one top-level directory")
        version_dir = install_root / version
        if version_dir.exists():
            shutil.rmtree(version_dir)
        extracted[0].rename(version_dir)
    finally:
        if staging.exists():
            shutil.rmtree(staging)

    current_link = install_root / "current"
    new_link = install_root / ".current.new"
    if new_link.exists() or new_link.is_symlink():
        new_link.unlink()
    new_link.symlink_to(version_dir)
    new_link.replace(current_link)
    return version_dir


DEFAULT_INSTALL_ROOT = Path("/opt/timelapse-agent")


def check_for_update(
    settings: Dict[str, Any],
    install_root: Path = DEFAULT_INSTALL_ROOT,
    work_dir: Optional[Path] = None,
) -> bool:
    work_dir = work_dir or Path(settings.get("work_dir", "/var/lib/timelapse-agent"))
    work_dir.mkdir(parents=True, exist_ok=True)
    download_dir = work_dir / "updates"

    url = settings["server_url"].rstrip("/") + f"/api/cameras/{settings['camera_id']}/update-manifest"
    try:
        manifest = request_json(url, timeout=15)
    except HTTPError as error:
        if error.code in (404, 503):
            return False
        logging.warning("Update manifest fetch failed: %s", error)
        return False
    except (URLError, TimeoutError, json.JSONDecodeError) as error:
        logging.warning("Update manifest fetch failed: %s", error)
        return False

    desired = manifest.get("version")
    bundle_url = manifest.get("url")
    sha = manifest.get("sha256")
    if not desired or not bundle_url or not sha:
        return False
    if desired == AGENT_VERSION:
        return False

    logging.info("Update available: %s -> %s", AGENT_VERSION, desired)
    download_dir.mkdir(parents=True, exist_ok=True)
    try:
        bundle_path = download_bundle(bundle_url, download_dir)
        verify_sha256(bundle_path, sha)
        install_bundle(bundle_path, desired, install_root)
    except (UpdateError, HTTPError, URLError, TimeoutError) as error:
        logging.error("Update failed: %s", error)
        return False
    finally:
        if download_dir.exists():
            for stale in download_dir.glob("*.tar.gz"):
                stale.unlink(missing_ok=True)

    logging.info("Update installed; exiting for systemd to restart on new version")
    return True


def read_wifi_rssi() -> Optional[int]:
    """Read Wi-Fi RSSI in dBm from `iw dev`. Linux-only; returns None on any failure.

    Parses the line `signal: -57 dBm` from `iw dev wlan0 link` output.
    """
    if not shutil.which("iw"):
        return None
    interface = os.environ.get("TIMELAPSE_WIFI_IFACE", "wlan0")
    try:
        result = subprocess.run(
            ["iw", "dev", interface, "link"],
            check=False, capture_output=True, text=True, timeout=2,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("signal:"):
            try:
                return int(line.split()[1])
            except (IndexError, ValueError):
                return None
    return None


def post_checkin(settings: Dict[str, Any], state: AgentState) -> None:
    url = settings["server_url"].rstrip("/") + f"/api/cameras/{settings['camera_id']}/checkin"
    payload = {
        "agent_version": AGENT_VERSION,
        "hostname": socket.gethostname(),
        "last_capture_at": state.last_capture_at,
        "last_upload_at": state.last_upload_at,
        "last_error": state.last_error,
        "pending_count": state.pending_count,
        "pending_bytes": state.pending_bytes,
        "in_schedule": state.in_schedule,
        "local_hour": state.local_hour,
        "current_light": state.current_light,
        "signal_dbm": read_wifi_rssi(),
    }
    try:
        post_json(url, payload)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
        logging.warning("Heartbeat failed: %s", error)


def post_multipart(
    url: str,
    file_path: Path,
    captured_at: str,
    timeout: int = 60,
) -> Dict[str, Any]:
    boundary = f"----timelapse-{uuid.uuid4().hex}"
    content_type = mimetypes.guess_type(str(file_path))[0] or "image/jpeg"
    body = bytearray()
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(
        (
            'Content-Disposition: form-data; name="image"; '
            f'filename="{file_path.name}"\r\n'
        ).encode("utf-8")
    )
    body.extend(f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"))
    body.extend(file_path.read_bytes())
    body.extend(f"\r\n--{boundary}--\r\n".encode("utf-8"))

    headers = {
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Content-Length": str(len(body)),
        "X-Captured-At": captured_at,
        "X-Agent-Hostname": socket.gethostname(),
    }
    request = Request(url, data=bytes(body), headers=headers, method="POST")
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def find_capture_command() -> Optional[str]:
    for command in ("rpicam-still", "libcamera-still", "raspistill"):
        path = shutil.which(command)
        if path:
            return path
    return None


def gphoto2_available() -> bool:
    """Return True if the gphoto2 binary is installed AND a camera is currently
    attached and visible to libgphoto2 over USB.

    `gphoto2 --auto-detect` always exits 0; an empty list is signalled by the
    output containing only the two-line header. We detect a camera by counting
    non-header lines.
    """
    if not shutil.which("gphoto2"):
        return False
    try:
        result = subprocess.run(
            ["gphoto2", "--auto-detect"],
            check=False, capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    if result.returncode != 0:
        return False
    # Output format:
    #   Model                          Port
    #   ----------------------------------------------------------
    #   Canon EOS R6                   usb:001,005
    for line in result.stdout.splitlines()[2:]:
        if line.strip():
            return True
    return False


def resolve_active_backend(config: Dict[str, Any]) -> Optional[str]:
    """Pick which capture backend to use given the server config.

    Returns 'rpicam', 'gphoto2', or None if neither is available.
    """
    requested = config.get("camera_backend", "auto")
    if requested == "rpicam":
        return "rpicam" if find_capture_command() else None
    if requested == "gphoto2":
        return "gphoto2" if gphoto2_available() else None
    # auto: prefer gphoto2 (more specialised) when a USB camera is present
    if gphoto2_available():
        return "gphoto2"
    if find_capture_command():
        return "rpicam"
    return None


@dataclass(frozen=True)
class CameraFileRef:
    folder: str
    filename: str


# gphoto2 prints a line like:
#   New file is in location /store_00020001/DCIM/100CANON/IMG_0042.CR3 on the camera
_NEW_FILE_RE = re.compile(
    r"^New file is in location (?P<path>/\S+?) on the camera\s*$"
)


def parse_new_file_location(stdout: str) -> Optional[CameraFileRef]:
    """Parse gphoto2 --capture-image stdout and return the camera file reference.

    Returns None if no `New file is in location` line is found.
    """
    for line in stdout.splitlines():
        match = _NEW_FILE_RE.match(line)
        if match:
            full = match.group("path")
            folder, _, filename = full.rpartition("/")
            return CameraFileRef(folder=folder or "/", filename=filename)
    return None


def gphoto2_capture_trigger(timeout: int = 30) -> CameraFileRef:
    """Trigger a capture on the connected DSLR; image stays on the camera SD.

    Returns the (folder, filename) reference parsed from gphoto2 stdout. Raises
    RuntimeError if the output can't be parsed, or subprocess.CalledProcessError
    on a non-zero exit (e.g. camera disconnected, SD full).
    """
    result = subprocess.run(
        ["gphoto2", "--capture-image"],
        check=True, capture_output=True, text=True, timeout=timeout,
    )
    ref = parse_new_file_location(result.stdout)
    if ref is None:
        raise RuntimeError(
            f"Could not parse gphoto2 capture output: {result.stdout!r}"
        )
    return ref


PENDING_CAMERA_FILES_NAME = "pending_camera_files.json"


def load_camera_pending(work_dir: Path) -> List[Dict[str, str]]:
    """Read the pending-camera-files queue. Returns [] if file missing or invalid."""
    path = work_dir / PENDING_CAMERA_FILES_NAME
    if not path.exists():
        return []
    try:
        data = load_json(path)
    except (json.JSONDecodeError, OSError):
        return []
    entries = data.get("entries") if isinstance(data, dict) else None
    return entries if isinstance(entries, list) else []


def save_camera_pending(work_dir: Path, entries: List[Dict[str, str]]) -> None:
    """Atomically write the pending-camera-files queue."""
    write_json(work_dir / PENDING_CAMERA_FILES_NAME, {"entries": entries})


def add_camera_pending(work_dir: Path, ref: CameraFileRef, captured_at: str) -> None:
    """Append a new pending entry for an image still on the camera."""
    entries = load_camera_pending(work_dir)
    entries.append({
        "folder": ref.folder,
        "filename": ref.filename,
        "captured_at": captured_at,
    })
    save_camera_pending(work_dir, entries)


def remove_camera_pending(work_dir: Path, ref: CameraFileRef) -> None:
    """Drop the entry matching (folder, filename). No-op if missing."""
    entries = load_camera_pending(work_dir)
    filtered = [
        e for e in entries
        if not (e.get("folder") == ref.folder and e.get("filename") == ref.filename)
    ]
    if len(filtered) != len(entries):
        save_camera_pending(work_dir, filtered)


GPHOTO2_STAGE_DIR = Path("/tmp/timelapse-agent-stage")


def gphoto2_download_file(ref: CameraFileRef, dest_path: Path, timeout: int = 120) -> Path:
    """Download a single file from the camera to dest_path.

    The destination should live on tmpfs (/tmp on Pi OS) so the SD card never
    sees the bytes. Caller is responsible for unlinking dest_path after upload.
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "gphoto2",
            "--folder", ref.folder,
            "--get-file", ref.filename,
            "--filename", str(dest_path),
            "--force-overwrite",
        ],
        check=True, capture_output=True, text=True, timeout=timeout,
    )
    return dest_path


def gphoto2_delete_file(ref: CameraFileRef, timeout: int = 30) -> None:
    """Delete a single file from the camera SD card.

    "File not found" is treated as success — the entry was already gone, which
    is exactly the state we wanted to reach. Other failures (USB claim errors,
    write-protected card, etc.) are propagated.
    """
    try:
        subprocess.run(
            [
                "gphoto2",
                "--folder", ref.folder,
                "--delete-file", ref.filename,
            ],
            check=True, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.CalledProcessError as error:
        stderr = (error.stderr or "").lower()
        if "file not found" in stderr:
            return
        raise


def gphoto2_disable_autopoweroff(timeout: int = 10) -> None:
    """Disable the camera's auto-poweroff so the USB connection stays alive.

    Best-effort: not all camera bodies expose this property. Failures are
    logged and swallowed.
    """
    try:
        subprocess.run(
            ["gphoto2", "--set-config", "autopoweroff=0"],
            check=True, capture_output=True, text=True, timeout=timeout,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as error:
        logging.info("Could not disable camera autopoweroff (often harmless): %s", error)


_DSLR_INIT_KEY_MAP: Dict[str, str] = {
    "capture_target": "capturetarget",
    "drive_mode": "drivemode",
    "focus_mode": "focusmode",
}

_DSLR_SEQUENCE_KEY_MAP: Dict[str, str] = {
    "shutterspeed": "shutterspeed",
    "aperture": "aperture",
    "iso": "iso",
    "exposure_compensation": "exposurecompensation",
    "whitebalance": "whitebalance",
    "image_format": "imageformat",
}

_DSLR_CHOICE_KEYS: List[str] = [
    "shutterspeed", "aperture", "iso", "exposurecompensation",
    "whitebalance", "imageformat", "capturetarget", "drivemode", "focusmode",
]


def gphoto2_read_choices(keys: Optional[List[str]] = None) -> Dict[str, List[str]]:
    if keys is None:
        keys = _DSLR_CHOICE_KEYS
    choices: Dict[str, List[str]] = {}
    for key in keys:
        try:
            result = subprocess.run(
                ["gphoto2", "--get-config", key],
                check=True, capture_output=True, text=True, timeout=10,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
            continue
        values = []
        for line in result.stdout.splitlines():
            if line.startswith("Choice:"):
                parts = line.split(None, 2)
                if len(parts) >= 3:
                    values.append(parts[2])
        if values:
            choices[key] = values
    return choices


def gphoto2_apply_init_settings(dslr: Dict[str, Any]) -> None:
    for field_name, gphoto_key in _DSLR_INIT_KEY_MAP.items():
        value = dslr.get(field_name)
        if value is None:
            continue
        try:
            subprocess.run(
                ["gphoto2", "--set-config", f"{gphoto_key}={value}"],
                check=True, capture_output=True, text=True, timeout=10,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as err:
            logging.warning("Could not set DSLR init setting %s=%s: %s", gphoto_key, value, err)


def gphoto2_apply_sequence_settings(dslr: Dict[str, Any]) -> None:
    for field_name, gphoto_key in _DSLR_SEQUENCE_KEY_MAP.items():
        value = dslr.get(field_name)
        if value is None:
            continue
        try:
            subprocess.run(
                ["gphoto2", "--set-config", f"{gphoto_key}={value}"],
                check=True, capture_output=True, text=True, timeout=10,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as err:
            logging.warning("Could not set DSLR sequence setting %s=%s: %s", gphoto_key, value, err)


def _gphoto2_get_current(key: str) -> Optional[str]:
    try:
        result = subprocess.run(
            ["gphoto2", "--get-config", key],
            check=True, capture_output=True, text=True, timeout=10,
        )
        for line in result.stdout.splitlines():
            if line.startswith("Current:"):
                return line.split(":", 1)[1].strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def gphoto2_read_telemetry() -> Dict[str, Any]:
    shots_str = _gphoto2_get_current("availableshots")
    counter_str = _gphoto2_get_current("shuttercounter")
    return {
        "battery_level": _gphoto2_get_current("batterylevel"),
        "available_shots": int(shots_str) if shots_str and shots_str.isdigit() else None,
        "shutter_counter": int(counter_str) if counter_str and counter_str.isdigit() else None,
        "exposure_mode": _gphoto2_get_current("autoexposuremode"),
    }


def measure_camera_pending(work_dir: Path) -> int:
    """Number of images queued on the camera awaiting upload."""
    return len(load_camera_pending(work_dir))


def upload_camera_pending(
    settings: Dict[str, Any], work_dir: Path, state: AgentState
) -> None:
    """For gphoto2 backend: stream each pending camera file → server → delete from camera."""
    entries = load_camera_pending(work_dir)
    if not entries:
        return

    url = settings["server_url"].rstrip("/") + f"/api/cameras/{settings['camera_id']}/upload"
    GPHOTO2_STAGE_DIR.mkdir(parents=True, exist_ok=True)

    for entry in list(entries):
        ref = CameraFileRef(folder=entry["folder"], filename=entry["filename"])
        captured_at = entry.get("captured_at") or now_local_iso()
        stage_path = GPHOTO2_STAGE_DIR / ref.filename

        try:
            gphoto2_download_file(ref, stage_path)
        except subprocess.CalledProcessError as error:
            stderr = (error.stderr or "").lower()
            if "could not find" in stderr or "file not found" in stderr:
                logging.warning(
                    "Camera file missing, dropping queue entry: %s/%s",
                    ref.folder, ref.filename,
                )
                remove_camera_pending(work_dir, ref)
                continue
            logging.warning("Download failed for %s: %s", ref.filename, error)
            state.last_error = f"download failed: {error}"
            return

        try:
            post_multipart(url, stage_path, captured_at)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
            logging.warning("Upload failed for %s: %s", ref.filename, error)
            state.last_error = f"upload failed: {error}"
            stage_path.unlink(missing_ok=True)
            return

        stage_path.unlink(missing_ok=True)
        try:
            gphoto2_delete_file(ref)
        except subprocess.CalledProcessError as error:
            # Upload succeeded but camera delete failed; keep going so we don't
            # re-upload, but flag it.
            logging.warning("Camera delete failed for %s: %s", ref.filename, error)
            state.last_error = f"camera delete failed: {error}"
        remove_camera_pending(work_dir, ref)
        state.last_upload_at = now_local_iso()
        state.last_error = None
        logging.info("Uploaded (camera) %s", ref.filename)


def build_capture_command(command: str, output_path: Path, config: Dict[str, Any]) -> list:
    quality = str(config.get("jpeg_quality", DEFAULT_REMOTE_CONFIG["jpeg_quality"]))

    if command.endswith("raspistill"):
        capture_command = [
            command,
            "-n",
            "-t",
            "1000",
            "-o",
            str(output_path),
            "-q",
            quality,
        ]
        if config.get("image_width"):
            capture_command.extend(["-w", str(config["image_width"])])
        if config.get("image_height"):
            capture_command.extend(["-h", str(config["image_height"])])
        return capture_command

    capture_command = [
        command,
        "--nopreview",
        "--timeout",
        "1000",
        "--output",
        str(output_path),
        "--quality",
        quality,
    ]
    if config.get("image_width"):
        capture_command.extend(["--width", str(config["image_width"])])
    if config.get("image_height"):
        capture_command.extend(["--height", str(config["image_height"])])
    return capture_command


def mean_y_from_yuv(raw: bytes, width: int, height: int) -> Optional[int]:
    """Mean Y luminance (0-255) from a raw YUV420 buffer's Y plane.

    YUV420 stores: width*height bytes of Y, then width*height/4 of U, then
    width*height/4 of V. We only need the Y plane.
    """
    y_plane_size = width * height
    if len(raw) < y_plane_size:
        return None
    plane = raw[:y_plane_size]
    return sum(plane) // len(plane)


def sample_light_level(command: str, width: int = 64, height: int = 48) -> Optional[int]:
    """Capture a tiny YUV thumbnail and return the mean Y luminance (0-255).

    Returns None if no capture tool is available or if the tool fails.
    """
    if command.endswith("raspistill"):
        cmd = [command, "-n", "-t", "200", "-w", str(width), "-h", str(height),
               "-e", "yuv", "-o", "-"]
    else:
        cmd = [command, "--nopreview", "--timeout", "200",
               "--width", str(width), "--height", str(height),
               "--encoding", "yuv420", "--output", "-"]
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, timeout=5)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as error:
        logging.warning("Light sample failed: %s", error)
        return None
    return mean_y_from_yuv(result.stdout, width, height)


def should_capture_for_scene(current_light: Optional[int], threshold: Optional[int]) -> bool:
    """Capture decision for scene-light mode.

    Conservative defaults: missing threshold or missing reading both return True
    (don't gate captures out due to misconfiguration or transient sample failures).
    """
    if threshold is None or current_light is None:
        return True
    return current_light >= threshold


def should_sample_light(backend: Optional[str]) -> bool:
    """Light sampling is only available on the rpicam backend (fast YUV thumbnail).
    DSLRs over gphoto2 have no equivalent fast preview, so we skip sampling
    and the user can't use scene-light gating with a DSLR."""
    return backend == "rpicam"


def now_local_iso() -> str:
    """ISO-8601 timestamp with explicit UTC offset, in agent local time."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def capture_frame(work_dir: Path, config: Dict[str, Any]):
    """Trigger a capture using the configured backend.

    Returns:
        - For 'rpicam' backend: pathlib.Path to the JPEG written under work_dir/pending/.
        - For 'gphoto2' backend: a CameraFileRef pointing to the newly captured
          file still residing on the camera SD card.
    """
    backend = resolve_active_backend(config)
    if backend is None:
        raise RuntimeError(
            "No camera backend available: install rpicam-apps-lite for Pi cameras "
            "or gphoto2 + a USB DSLR"
        )

    if backend == "gphoto2":
        ref = gphoto2_capture_trigger()
        add_camera_pending(work_dir, ref, captured_at=now_local_iso())
        return ref

    # rpicam path (unchanged behaviour)
    command = find_capture_command()
    captured_at_filename = datetime.now().strftime("%Y%m%dT%H%M%S")
    output_path = work_dir / "pending" / f"{captured_at_filename}.jpg"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_suffix(".tmp.jpg")

    subprocess.run(build_capture_command(command, temp_path, config), check=True)
    temp_path.replace(output_path)

    metadata = {
        "captured_at": now_local_iso(),
        "hostname": socket.gethostname(),
    }
    write_json(output_path.with_suffix(".json"), metadata)
    return output_path


def fetch_remote_config(settings: Dict[str, Any], cache_path: Path) -> Dict[str, Any]:
    url = settings["server_url"].rstrip("/") + f"/api/cameras/{settings['camera_id']}/config"
    try:
        config = request_json(url)
        write_json(cache_path, config)
        return config
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
        logging.warning("Could not fetch server config: %s", error)
        if cache_path.exists():
            return load_json(cache_path)
        return DEFAULT_REMOTE_CONFIG.copy()


def upload_pending(settings: Dict[str, Any], work_dir: Path, state: AgentState) -> None:
    """Drain both the local pending/ directory (rpicam path) and the camera-
    resident pending queue (gphoto2 path). Either may be empty."""
    pending_dir = work_dir / "pending"
    pending_dir.mkdir(parents=True, exist_ok=True)
    url = settings["server_url"].rstrip("/") + f"/api/cameras/{settings['camera_id']}/upload"

    for image_path in sorted(pending_dir.glob("*.jpg")):
        metadata_path = image_path.with_suffix(".json")
        metadata = load_json(metadata_path) if metadata_path.exists() else {}
        captured_at = metadata.get("captured_at", now_local_iso())

        try:
            post_multipart(url, image_path, captured_at)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
            logging.warning("Upload failed for %s: %s", image_path.name, error)
            state.last_error = f"upload failed: {error}"
            return

        image_path.unlink(missing_ok=True)
        metadata_path.unlink(missing_ok=True)
        state.last_upload_at = now_local_iso()
        state.last_error = None
        logging.info("Uploaded %s", image_path.name)

    upload_camera_pending(settings, work_dir, state)


def next_due_time(last_capture: Optional[float], interval_seconds: int, now: float) -> float:
    if last_capture is None:
        return now
    return max(now, last_capture + interval_seconds)


def run_agent(settings: Dict[str, Any]) -> None:
    work_dir = Path(settings.get("work_dir", "/var/lib/timelapse-agent"))
    work_dir.mkdir(parents=True, exist_ok=True)
    cache_path = work_dir / "server-config.json"

    poll_seconds = int(settings.get("config_poll_seconds", 60))
    max_pending_bytes = resolve_max_pending_bytes(settings, work_dir)
    state = AgentState()
    remote_config = fetch_remote_config(settings, cache_path)
    last_capture: Optional[float] = None
    next_capture = time.monotonic()
    next_config_poll = time.monotonic() + poll_seconds

    logging.info(
        "Agent v%s started for camera_id=%s (max_pending_bytes=%s)",
        AGENT_VERSION, settings["camera_id"], max_pending_bytes,
    )
    if resolve_active_backend(remote_config) == "gphoto2":
        gphoto2_disable_autopoweroff()
    state.pending_count, state.pending_bytes = measure_pending(work_dir)
    state.pending_count += measure_camera_pending(work_dir)
    startup_now = datetime.now().astimezone()
    state.local_hour = startup_now.hour
    startup_schedule_mode = remote_config.get("schedule_mode")
    if startup_schedule_mode == "daylight":
        startup_offset_h = startup_now.utcoffset().total_seconds() / 3600
        startup_effective_hours = daylight_capture_hours(
            today=startup_now.date(),
            latitude=remote_config.get("latitude"),
            longitude=remote_config.get("longitude"),
            utc_offset_hours=startup_offset_h,
        )
    else:
        startup_effective_hours = remote_config.get("capture_hours")
    state.in_schedule = is_in_schedule(startup_now, startup_effective_hours, remote_config.get("schedule_days"))
    post_checkin(settings, state)

    while True:
        now = time.monotonic()

        if now >= next_config_poll:
            previous_interval = int(remote_config.get("interval_seconds", 900))
            remote_config = fetch_remote_config(settings, cache_path)
            new_interval = int(remote_config.get("interval_seconds", previous_interval))
            if new_interval != previous_interval:
                next_capture = next_due_time(last_capture, new_interval, now)
                logging.info("Capture interval changed to %s seconds", new_interval)
            state.pending_count, state.pending_bytes = measure_pending(work_dir)
            state.pending_count += measure_camera_pending(work_dir)
            post_checkin(settings, state)
            if check_for_update(settings):
                logging.info("Exiting to allow systemd restart")
                return
            next_config_poll = now + poll_seconds

        upload_pending(settings, work_dir, state)
        state.pending_count, state.pending_bytes = measure_pending(work_dir)
        state.pending_count += measure_camera_pending(work_dir)

        enabled = bool(remote_config.get("enabled", True))
        interval_seconds = int(remote_config.get("interval_seconds", 900))
        local_now = datetime.now().astimezone()
        schedule_mode = remote_config.get("schedule_mode")
        if schedule_mode == "daylight":
            offset_h = local_now.utcoffset().total_seconds() / 3600
            effective_hours = daylight_capture_hours(
                today=local_now.date(),
                latitude=remote_config.get("latitude"),
                longitude=remote_config.get("longitude"),
                utc_offset_hours=offset_h,
            )
        else:
            effective_hours = remote_config.get("capture_hours")
        in_schedule = is_in_schedule(local_now, effective_hours, remote_config.get("schedule_days"))
        # Update state every loop so heartbeat reflects the current view.
        state.local_hour = local_now.hour
        if in_schedule != state.in_schedule:
            state.in_schedule = in_schedule
            if not in_schedule and effective_hours:
                next_hour = next_allowed_hour(local_now.hour, effective_hours)
                logging.info(
                    "Capture paused — outside schedule (local hour %d, allowed %s, resume at %02d:00)",
                    local_now.hour, effective_hours, next_hour,
                )
            elif in_schedule and effective_hours:
                logging.info(
                    "Capture resumed — local hour %d is within schedule %s",
                    local_now.hour, effective_hours,
                )
        if enabled and not in_schedule and now >= next_capture:
            # Outside the schedule: skip this slot, re-check at the next interval.
            next_capture = now + interval_seconds
        # Always sample scene luminance before each capture so the user can see
        # a live reading regardless of schedule_mode. Sampling adds ~200ms;
        # negligible compared to the capture interval.
        if enabled and in_schedule and now >= next_capture:
            active_backend = resolve_active_backend(remote_config)
            if should_sample_light(active_backend):
                tool = find_capture_command()
                if tool:
                    state.current_light = sample_light_level(tool)
            else:
                state.current_light = None
            # Scene-light mode: skip the actual capture if below threshold.
            # On gphoto2 backend current_light is always None and the
            # conservative defaults in should_capture_for_scene mean we never
            # gate captures out — equivalent to scene mode being a no-op.
            if (
                schedule_mode == "scene"
                and active_backend == "rpicam"
                and not should_capture_for_scene(
                    state.current_light, remote_config.get("light_threshold")
                )
            ):
                logging.info(
                    "Scene-light gate: Y=%s < threshold=%s, skipping",
                    state.current_light, remote_config.get("light_threshold"),
                )
                next_capture = now + interval_seconds
        if enabled and in_schedule and now >= next_capture:
            try:
                image_path = capture_frame(work_dir, remote_config)
                state.last_capture_at = now_local_iso()
                state.last_error = None
                logging.info("Captured %s", image_path.name)
            except Exception as error:
                state.last_error = str(error)
                logging.exception("Capture failed")
                next_capture = now + min(300, interval_seconds)
            else:
                last_capture = time.monotonic()
                evicted_count, evicted_bytes = evict_pending(work_dir, max_pending_bytes)
                if evicted_count:
                    logging.warning(
                        "Evicted %d oldest pending captures (%d bytes) to stay under %d-byte cap",
                        evicted_count, evicted_bytes, max_pending_bytes,
                    )
                upload_pending(settings, work_dir, state)
                state.pending_count, state.pending_bytes = measure_pending(work_dir)
                state.pending_count += measure_camera_pending(work_dir)
                next_capture = last_capture + interval_seconds

        sleep_until = min(next_capture, next_config_poll)
        time.sleep(max(1, min(5, sleep_until - time.monotonic())))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Hydroponic timelapse Pi agent")
    parser.add_argument(
        "--config",
        default="/etc/timelapse-agent/config.json",
        help="Path to agent config JSON",
    )
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    args = parse_args()
    settings = load_json(Path(args.config))
    for key in ("camera_id", "server_url"):
        if not settings.get(key):
            logging.error("Missing required config key: %s", key)
            return 2
    run_agent(settings)
    return 0


if __name__ == "__main__":
    sys.exit(main())
