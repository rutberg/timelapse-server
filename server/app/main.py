from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import tempfile
from datetime import datetime, timezone
from ipaddress import ip_address, ip_network
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, urlunparse

from fastapi import (
    BackgroundTasks,
    FastAPI,
    File,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator, model_validator

from app.agents import AgentStore, PendingAgent
from app.provision_script import build_install_script
from app.ssh_keys import generate_keypair, read_public_key
from app.ssh_provision import ProvisionError, resolve_target, run_provision

app = FastAPI(title="Hydroponic Timelapse Server")

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("TIMELAPSE_DATA_DIR", "./data")).resolve()
STATIC_DIR = Path(__file__).resolve().parent / "static"
STORE_PATH = DATA_DIR / "config.json"
VALID_CAMERA_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}$")
VALID_FRAME_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
VALID_FRAME_FILENAME_RE = re.compile(r"^\d{8}T\d{6}Z?(?:-\d+)?\.jpg$")
DEFAULT_ALLOWED_NETWORKS = (
    "127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,::1/128,fc00::/7,fe80::/10"
)
ALLOWED_NETWORKS = [
    ip_network(value.strip())
    for value in os.environ.get(
        "TIMELAPSE_ALLOWED_NETWORKS", DEFAULT_ALLOWED_NETWORKS
    ).split(",")
    if value.strip()
]


class DslrSettings(BaseModel):
    capture_target: str = "Memory card"
    drive_mode: str = "Single"
    focus_mode: str = "Manual"
    shutterspeed: Optional[str] = None
    aperture: Optional[str] = None
    iso: Optional[str] = None
    exposure_compensation: Optional[str] = None
    whitebalance: Optional[str] = None
    image_format: Optional[str] = None
    reinit_token: Optional[str] = None


class DslrStatus(BaseModel):
    battery_level: Optional[str] = None
    available_shots: Optional[int] = None
    shutter_counter: Optional[int] = None
    exposure_mode: Optional[str] = None
    lens_name: Optional[str] = None
    camera_model: Optional[str] = None
    choices: Dict[str, List[str]] = Field(default_factory=dict)
    current_values: Dict[str, str] = Field(default_factory=dict)
    last_reinit_token: Optional[str] = None
    last_init_at: Optional[str] = None


class CameraConfig(BaseModel):
    enabled: bool = True
    interval_seconds: int = Field(900, ge=30, le=86_400)
    image_width: Optional[int] = Field(None, ge=320, le=10_000)
    image_height: Optional[int] = Field(None, ge=240, le=10_000)
    jpeg_quality: int = Field(85, ge=1, le=100)
    desired_agent_version: Optional[str] = Field(None, pattern=r"^[A-Za-z0-9._-]+$")
    capture_hours: Optional[List[int]] = Field(
        default=None,
        description=(
            "Hours of day (0-23, agent local time) when capture is allowed. "
            "None = always. Empty list rejected — use enabled=false to pause."
        ),
    )
    schedule_mode: Optional[str] = Field(
        default=None,
        description="One of 'daylight', 'hours', 'scene'. None = legacy/unset.",
    )
    schedule_days: Optional[List[int]] = Field(
        default=None,
        description="ISO weekdays (1=Mon..7=Sun) on which capture is allowed. None = every day.",
    )
    light_threshold: Optional[int] = Field(
        default=None,
        ge=0,
        le=255,
        description="Mean Y luma 0-255. Required when schedule_mode='scene'.",
    )
    display_name: Optional[str] = Field(
        default=None,
        max_length=120,
        description="Human-friendly camera label. Falls back to camera_id.",
    )
    latitude: Optional[float] = Field(default=None, ge=-90.0, le=90.0)
    longitude: Optional[float] = Field(default=None, ge=-180.0, le=180.0)
    camera_backend: str = Field(
        default="auto",
        description=(
            "Capture backend selection: 'auto' (gphoto2 if a USB camera is "
            "detected, otherwise rpicam), 'rpicam' (Pi camera via rpicam-still/"
            "libcamera-still/raspistill), or 'gphoto2' (USB DSLR via gphoto2)."
        ),
    )
    dslr: Optional[DslrSettings] = None

    @field_validator("camera_backend")
    @classmethod
    def _validate_camera_backend(cls, value):
        allowed = {"auto", "rpicam", "gphoto2"}
        if value not in allowed:
            raise ValueError(
                f"camera_backend must be one of {sorted(allowed)}, got {value!r}"
            )
        return value

    @field_validator("capture_hours")
    @classmethod
    def _validate_capture_hours(cls, value):
        if value is None:
            return value
        if not value:
            raise ValueError(
                "capture_hours must be null or contain at least one hour; "
                "use enabled=false to pause"
            )
        seen = set()
        for hour in value:
            if not isinstance(hour, int) or isinstance(hour, bool):
                raise ValueError(
                    f"capture_hours entries must be ints 0-23, got {hour!r}"
                )
            if hour < 0 or hour > 23:
                raise ValueError(f"capture_hours entries must be 0-23, got {hour}")
            if hour in seen:
                raise ValueError(f"capture_hours has duplicate {hour}")
            seen.add(hour)
        return sorted(seen)

    @field_validator("schedule_mode")
    @classmethod
    def _validate_schedule_mode(cls, value):
        if value is None:
            return value
        if value not in ("daylight", "hours", "scene"):
            raise ValueError(
                f"schedule_mode must be 'daylight', 'hours', or 'scene'; got {value!r}"
            )
        return value

    @field_validator("schedule_days")
    @classmethod
    def _validate_schedule_days(cls, value):
        if value is None:
            return value
        if not value:
            raise ValueError(
                "schedule_days must be null or contain at least one weekday; "
                "use enabled=false to pause"
            )
        seen = set()
        for day in value:
            if not isinstance(day, int) or isinstance(day, bool):
                raise ValueError(
                    f"schedule_days entries must be ISO weekdays 1-7, got {day!r}"
                )
            if day < 1 or day > 7:
                raise ValueError(
                    f"schedule_days entries must be 1 (Mon) - 7 (Sun), got {day}"
                )
            if day in seen:
                raise ValueError(f"schedule_days has duplicate {day}")
            seen.add(day)
        return sorted(seen)

    @model_validator(mode="after")
    def _enforce_mode_invariants(self):
        if self.schedule_mode == "hours":
            if not self.capture_hours:
                raise ValueError(
                    "schedule_mode='hours' requires non-empty capture_hours"
                )
        elif self.schedule_mode == "daylight":
            # daylight derives hours per-day from sunrise/sunset; explicit hours are dropped
            self.capture_hours = None
        elif self.schedule_mode == "scene":
            if self.light_threshold is None:
                raise ValueError("schedule_mode='scene' requires light_threshold")
            # scene gates on luminance, not clock; explicit hours are dropped
            self.capture_hours = None
        return self


class CameraStatus(BaseModel):
    hostname: Optional[str] = None
    source_ip: Optional[str] = None
    last_seen: Optional[str] = None
    last_capture_at: Optional[str] = None
    last_upload_at: Optional[str] = None
    last_error: Optional[str] = None
    agent_version: Optional[str] = None
    pending_count: int = 0
    pending_bytes: int = 0
    in_schedule: Optional[bool] = None
    local_hour: Optional[int] = None
    current_light: Optional[int] = Field(default=None, ge=0, le=255)
    signal_dbm: Optional[int] = Field(default=None, ge=-120, le=0)
    active_backend: Optional[str] = None
    dslr: Optional[DslrStatus] = None


class CameraRecord(BaseModel):
    config: CameraConfig = Field(default_factory=CameraConfig)
    status: CameraStatus = Field(default_factory=CameraStatus)


class CheckinRequest(BaseModel):
    agent_version: Optional[str] = None
    hostname: Optional[str] = None
    last_capture_at: Optional[str] = None
    last_upload_at: Optional[str] = None
    last_error: Optional[str] = None
    pending_count: Optional[int] = Field(default=None, ge=0)
    pending_bytes: Optional[int] = Field(default=None, ge=0)
    in_schedule: Optional[bool] = None
    local_hour: Optional[int] = Field(default=None, ge=0, le=23)
    current_light: Optional[int] = Field(default=None, ge=0, le=255)
    signal_dbm: Optional[int] = Field(default=None, ge=-120, le=0)
    active_backend: Optional[str] = None
    dslr: Optional[DslrStatus] = None


HOSTNAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9.-]{0,253}$")


class CreateAgentRequest(BaseModel):
    agent_id: str
    display_name: str
    expected_hostname: str
    ip_fallback: Optional[str] = None
    ssh_user: str = "pi"


class ProvisionRequest(BaseModel):
    ip_fallback: Optional[str] = None
    sudo_password: Optional[str] = Field(default=None, max_length=256)


def validate_hostname(value: str) -> str:
    if not HOSTNAME_RE.match(value):
        raise HTTPException(status_code=400, detail="Invalid hostname")
    return value


def validate_ip(value: Optional[str]) -> Optional[str]:
    if value is None or value == "":
        return None
    try:
        ip_address(value)
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Invalid IP address") from error
    return value


def validate_ssh_user(value: str) -> str:
    if not re.match(r"^[a-z_][a-z0-9_-]{0,30}$", value):
        raise HTTPException(status_code=400, detail="Invalid SSH user")
    return value


class VideoRequest(BaseModel):
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    fps: int = Field(24, ge=1, le=60)
    name: Optional[str] = None
    format: str = Field("mp4", pattern=r"^(mp4|gif)$")


def model_dict(model: BaseModel) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def detect_lan_ip() -> Optional[str]:
    """Return the host's primary outgoing IPv4 address, or None.

    Uses the standard UDP-connect trick: open a datagram socket toward a
    non-routable address. The kernel picks the egress interface; no
    packet is sent. The local socket name is the IP we'd use to reach
    that host — i.e. our LAN address.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("10.255.255.255", 1))
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()


def _is_loopback_host(host: str) -> bool:
    if not host:
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def resolve_public_server_url(base_url: str) -> str:
    """Pick the URL the agent on the Pi should call back on.

    Priority:
      1. TIMELAPSE_PUBLIC_URL env var (operator override).
      2. If `base_url`'s host is loopback (127.0.0.0/8 or "localhost"),
         substitute the server's primary LAN IP.
      3. Otherwise pass `base_url` through unchanged.
    """
    override = os.environ.get("TIMELAPSE_PUBLIC_URL")
    if override:
        return override.rstrip("/")
    parsed = urlparse(base_url)
    host = parsed.hostname or ""
    if _is_loopback_host(host):
        lan_ip = detect_lan_ip()
        if lan_ip:
            netloc = f"{lan_ip}:{parsed.port}" if parsed.port else lan_ip
            return urlunparse(parsed._replace(netloc=netloc)).rstrip("/")
    return base_url


def lan_client_allowed(request: Request) -> bool:
    client_host = request.client.host if request.client else ""
    if client_host == "testclient":
        return True
    try:
        client_ip = ip_address(client_host)
    except ValueError:
        return False
    return any(client_ip in network for network in ALLOWED_NETWORKS)


def safe_identifier(value: str) -> str:
    if not value or not VALID_CAMERA_ID_RE.match(value):
        raise HTTPException(
            status_code=400,
            detail=(
                "Invalid camera id: must be 1-63 characters, start with "
                "alphanumeric, and contain only [a-zA-Z0-9_.-]"
            ),
        )
    return value


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "images").mkdir(exist_ok=True)
    (DATA_DIR / "thumbnails").mkdir(exist_ok=True)
    (DATA_DIR / "videos").mkdir(exist_ok=True)


def directory_size_bytes(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for entry in path.rglob("*"):
        try:
            if entry.is_file():
                total += entry.stat().st_size
        except OSError:
            continue
    return total


def compute_stats() -> Dict[str, int]:
    """Storage usage and capacity for the data directory's filesystem."""
    used = (
        directory_size_bytes(DATA_DIR / "images")
        + directory_size_bytes(DATA_DIR / "thumbnails")
        + directory_size_bytes(DATA_DIR / "videos")
    )
    try:
        usage = shutil.disk_usage(str(DATA_DIR))
        capacity = usage.total
    except OSError:
        capacity = used  # degenerate fallback so the UI shows 100%
    return {"storage_bytes": int(used), "storage_capacity_bytes": int(capacity)}


LEGACY_CONFIG_KEYS = {
    "enabled",
    "interval_seconds",
    "image_width",
    "image_height",
    "jpeg_quality",
    "config_version",
}


def migrate_camera_record(raw: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(raw.get("config"), dict) and isinstance(raw.get("status"), dict):
        return raw
    legacy = {key: raw[key] for key in LEGACY_CONFIG_KEYS if key in raw}
    legacy.pop("config_version", None)
    return {
        "config": model_dict(CameraConfig(**legacy)),
        "status": model_dict(CameraStatus()),
    }


def load_store() -> Dict[str, Any]:
    ensure_data_dir()
    if not STORE_PATH.exists():
        return {"cameras": {}}
    with STORE_PATH.open("r", encoding="utf-8") as store_file:
        store = json.load(store_file)
    cameras = store.setdefault("cameras", {})
    migrated = False
    for camera_id, record in list(cameras.items()):
        new_record = migrate_camera_record(record)
        if new_record is not record:
            cameras[camera_id] = new_record
            migrated = True
    if migrated:
        save_store(store)
    return store


def save_store(store: Dict[str, Any]) -> None:
    ensure_data_dir()
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=str(DATA_DIR),
        delete=False,
    ) as temp_file:
        json.dump(store, temp_file, indent=2, sort_keys=True)
        temp_file.write("\n")
        temp_path = Path(temp_file.name)
    temp_path.replace(STORE_PATH)


def get_camera_config(camera_id: str) -> CameraConfig:
    camera_id = safe_identifier(camera_id)
    store = load_store()
    cameras = store.setdefault("cameras", {})
    if camera_id not in cameras:
        cameras[camera_id] = model_dict(CameraRecord())
        save_store(store)
    return CameraConfig(**cameras[camera_id]["config"])


def set_camera_config(camera_id: str, config: CameraConfig) -> CameraConfig:
    camera_id = safe_identifier(camera_id)
    store = load_store()
    cameras = store.setdefault("cameras", {})
    record = cameras.get(camera_id) or model_dict(CameraRecord())
    record["config"] = model_dict(config)
    cameras[camera_id] = record
    save_store(store)
    return CameraConfig(**record["config"])


def parse_capture_time(value: Optional[str]) -> datetime:
    """Parse the X-Captured-At header.

    The agent sends an ISO-8601 string in its local time with explicit
    UTC offset (e.g. "2026-05-04T07:35:00+02:00"). We preserve that
    offset so the file path and filename reflect the camera's local
    wall-clock time.

    Falls back to the server's local time when the header is missing
    or malformed.
    """
    if not value:
        return datetime.now().astimezone()
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return datetime.now().astimezone()
    if parsed.tzinfo is None:
        # Header had no offset — assume server local time so date math works.
        parsed = parsed.astimezone()
    return parsed


def image_path(camera_id: str, captured_at: datetime) -> Path:
    # captured_at carries the camera's wall-clock time + its UTC offset.
    # Use the wall-clock components directly so files land in the day the
    # camera saw, not the day UTC saw.
    day = captured_at.strftime("%Y-%m-%d")
    timestamp = captured_at.strftime("%Y%m%dT%H%M%S")
    directory = DATA_DIR / "images" / camera_id / day
    directory.mkdir(parents=True, exist_ok=True)
    candidate = directory / f"{timestamp}.jpg"
    suffix = 1
    while candidate.exists():
        candidate = directory / f"{timestamp}-{suffix}.jpg"
        suffix += 1
    return candidate


def latest_image(camera_id: str) -> Optional[Path]:
    base = DATA_DIR / "images" / camera_id
    if not base.exists():
        return None
    images = sorted(base.glob("*/*.jpg"))
    if not images:
        return None
    return images[-1]


def list_camera_images(camera_id: str) -> List[Path]:
    base = DATA_DIR / "images" / camera_id
    if not base.exists():
        return []
    return sorted(base.glob("*/*.jpg"))


def list_valid_camera_frames(camera_id: str) -> List[Path]:
    return [
        path
        for path in list_camera_images(camera_id)
        if VALID_FRAME_DAY_RE.match(path.parent.name)
        and VALID_FRAME_FILENAME_RE.match(path.name)
    ]


def frame_key(path: Path) -> str:
    return f"{path.parent.name}/{path.name}"


def validate_frame_day(value: str) -> str:
    if not VALID_FRAME_DAY_RE.match(value):
        raise HTTPException(status_code=400, detail="Invalid frame day")
    return value


def validate_frame_filename(value: str) -> str:
    if not VALID_FRAME_FILENAME_RE.match(value):
        raise HTTPException(status_code=400, detail="Invalid frame filename")
    return value


def parse_frame_datetime(path: Path) -> Optional[datetime]:
    timestamp = path.stem.split("-", 1)[0].removesuffix("Z")
    try:
        return datetime.strptime(timestamp, "%Y%m%dT%H%M%S")
    except ValueError:
        return None


def parse_frame_timestamp(path: Path) -> Optional[str]:
    captured = parse_frame_datetime(path)
    if captured is None:
        return None
    return captured.isoformat()


def frame_to_response(camera_id: str, path: Path) -> Dict[str, Any]:
    stat = path.stat()
    day = path.parent.name
    filename = path.name
    return {
        "day": day,
        "filename": filename,
        "cursor": frame_key(path),
        "captured_at": parse_frame_timestamp(path),
        "stored_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "size_bytes": stat.st_size,
        "url": f"/api/cameras/{camera_id}/frames/{day}/{filename}",
        "thumbnail_url": f"/api/cameras/{camera_id}/frames/{day}/{filename}/thumbnail",
    }


def thumbnail_path(camera_id: str, day: str, filename: str) -> Path:
    return DATA_DIR / "thumbnails" / camera_id / day / filename


def thumbnail_current(source: Path, thumbnail: Path) -> bool:
    try:
        return (
            thumbnail.exists()
            and thumbnail.is_file()
            and thumbnail.stat().st_size > 0
            and thumbnail.stat().st_mtime >= source.stat().st_mtime
        )
    except OSError:
        return False


def ensure_frame_thumbnail(source: Path, thumbnail: Path) -> bool:
    if thumbnail_current(source, thumbnail):
        return True
    if not shutil.which("ffmpeg"):
        return False

    thumbnail.parent.mkdir(parents=True, exist_ok=True)
    temp_path = thumbnail.with_name(f".{thumbnail.name}.tmp.jpg")
    temp_path.unlink(missing_ok=True)
    command = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-vf",
        "scale=w=320:h=320:force_original_aspect_ratio=decrease",
        "-frames:v",
        "1",
        "-q:v",
        "5",
        str(temp_path),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=30)
        if not temp_path.exists() or temp_path.stat().st_size == 0:
            return False
        temp_path.replace(thumbnail)
        return True
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        logging.warning("Thumbnail generation failed for %s: %s", source, error)
        return False
    finally:
        temp_path.unlink(missing_ok=True)


def delete_frame_thumbnail(camera_id: str, day: str, filename: str) -> None:
    path = thumbnail_path(camera_id, day, filename)
    path.unlink(missing_ok=True)
    thumbnails_root = DATA_DIR / "thumbnails" / camera_id
    for directory in (path.parent, path.parent.parent):
        if directory == thumbnails_root.parent:
            break
        try:
            directory.rmdir()
        except OSError:
            break


def frame_gap_threshold_seconds(camera_id: str) -> int:
    try:
        interval = get_camera_config(camera_id).interval_seconds
    except Exception:
        interval = 60
    return max(interval * 2, 120)


def frame_gap_responses(
    paths: List[Path], threshold_seconds: int
) -> List[Dict[str, Any]]:
    gaps = []
    previous_dt: Optional[datetime] = None
    previous_path: Optional[Path] = None
    for path in sorted(paths, key=frame_key):
        captured = parse_frame_datetime(path)
        if captured is None:
            continue
        if previous_dt is not None and previous_path is not None:
            delta = int((captured - previous_dt).total_seconds())
            if delta > threshold_seconds:
                gaps.append(
                    {
                        "after": frame_key(previous_path),
                        "before": frame_key(path),
                        "start": previous_dt.isoformat(),
                        "end": captured.isoformat(),
                        "duration_seconds": delta,
                    }
                )
        previous_dt = captured
        previous_path = path
    return gaps


def frame_day_response(camera_id: str, day: str, paths: List[Path]) -> Dict[str, Any]:
    paths = sorted(paths, key=frame_key)
    captured_values = [
        captured
        for captured in (parse_frame_datetime(path) for path in paths)
        if captured is not None
    ]
    first = captured_values[0] if captured_values else None
    last = captured_values[-1] if captured_values else None
    total_size = 0
    for path in paths:
        try:
            total_size += path.stat().st_size
        except OSError:
            continue
    gaps = frame_gap_responses(paths, frame_gap_threshold_seconds(camera_id))
    return {
        "day": day,
        "month": day[:7],
        "count": len(paths),
        "size_bytes": total_size,
        "first_captured_at": first.isoformat() if first else None,
        "last_captured_at": last.isoformat() if last else None,
        "gaps": gaps,
        "gap_count": len(gaps),
    }


ONLINE_GRACE_SECONDS = 180


def is_camera_online(status: Dict[str, Any], poll_seconds: int = 60) -> bool:
    last_seen = status.get("last_seen")
    if not last_seen:
        return False
    try:
        seen_at = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
    except ValueError:
        return False
    threshold = max(ONLINE_GRACE_SECONDS, poll_seconds * 3)
    return (datetime.now(timezone.utc) - seen_at).total_seconds() <= threshold


def camera_summary(camera_id: str, record: Dict[str, Any]) -> Dict[str, Any]:
    images = list_camera_images(camera_id)
    latest = images[-1] if images else None
    config = record.get("config", {})
    status = dict(record.get("status", {}))
    status["is_online"] = is_camera_online(status)
    return {
        "camera_id": camera_id,
        "config": config,
        "status": status,
        "image_count": len(images),
        "latest_image": str(latest.relative_to(DATA_DIR)) if latest else None,
    }


def selected_images(camera_id: str, request: VideoRequest) -> List[Path]:
    images = list_camera_images(camera_id)
    if not request.start_date and not request.end_date:
        return images
    selected = []
    for path in images:
        day = path.parent.name
        if request.start_date and day < request.start_date:
            continue
        if request.end_date and day > request.end_date:
            continue
        selected.append(path)
    return selected


def ffmpeg_escape(path: Path) -> str:
    return str(path.resolve()).replace("'", "'\\''")


@app.middleware("http")
async def lan_only_middleware(request: Request, call_next):
    if not lan_client_allowed(request):
        return JSONResponse({"detail": "LAN access only"}, status_code=403)
    return await call_next(request)


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "v2" / "index.html", media_type="text/html")


@app.get("/api/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/api/server-info")
def server_info(request: Request) -> Dict[str, Any]:
    url = resolve_public_server_url(str(request.base_url).rstrip("/"))
    return {"server_url": url, "lan_ip": detect_lan_ip()}


def agent_store() -> AgentStore:
    return AgentStore(DATA_DIR)


def agent_to_response(
    agent: PendingAgent, include_public_key: bool = False
) -> Dict[str, Any]:
    body = {
        "agent_id": agent.agent_id,
        "display_name": agent.display_name,
        "expected_hostname": agent.expected_hostname,
        "ip_fallback": agent.ip_fallback,
        "ssh_user": agent.ssh_user,
        "status": agent.status,
        "created_at": agent.created_at,
        "last_provision_attempt_at": agent.last_provision_attempt_at,
        "last_provision_error": agent.last_provision_error,
    }
    if include_public_key:
        public_path = DATA_DIR / "agents" / agent.agent_id / "id_ed25519.pub"
        if public_path.exists():
            body["public_key"] = read_public_key(public_path)
    return body


@app.post("/api/agents", status_code=201)
def create_agent(payload: CreateAgentRequest) -> Dict[str, Any]:
    agent_id = safe_identifier(payload.agent_id)
    expected_hostname = validate_hostname(payload.expected_hostname)
    ip_fallback = validate_ip(payload.ip_fallback)
    ssh_user = validate_ssh_user(payload.ssh_user)

    store = agent_store()
    try:
        agent = store.create(
            agent_id=agent_id,
            display_name=payload.display_name,
            expected_hostname=expected_hostname,
            ip_fallback=ip_fallback,
            ssh_user=ssh_user,
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

    generate_keypair(
        DATA_DIR / "agents" / agent_id,
        comment=f"timelapse-agent-{agent_id}",
    )
    return agent_to_response(agent, include_public_key=True)


@app.get("/api/agents")
def list_agents() -> Dict[str, Any]:
    return {"agents": [agent_to_response(agent) for agent in agent_store().list()]}


@app.get("/api/agents/{agent_id}")
def read_agent(agent_id: str) -> Dict[str, Any]:
    agent_id = safe_identifier(agent_id)
    try:
        agent = agent_store().get(agent_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Agent not found") from error
    return agent_to_response(agent, include_public_key=True)


@app.post("/api/agents/{agent_id}/provision")
def provision_agent(
    agent_id: str, payload: ProvisionRequest, request: Request
) -> Dict[str, Any]:
    agent_id = safe_identifier(agent_id)
    store = agent_store()
    try:
        agent = store.get(agent_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Agent not found") from error

    if payload.ip_fallback is not None:
        ip_fallback = validate_ip(payload.ip_fallback)
        agent.ip_fallback = ip_fallback
        store._write(agent)  # type: ignore[attr-defined]

    store.update_status(agent_id, status="provisioning", last_provision_error=None)

    server_url = resolve_public_server_url(str(request.base_url).rstrip("/"))
    agent_dir = DATA_DIR / "agents" / agent_id
    private_key_path = agent_dir / "id_ed25519"
    known_hosts_path = agent_dir / "known_hosts"

    try:
        target = resolve_target(agent.expected_hostname, agent.ip_fallback)
        agent_version = (
            (REPO_ROOT / "agent" / "VERSION").read_text(encoding="utf-8").strip()
        )
        install_script = build_install_script(
            camera_id=agent_id,
            server_url=server_url,
            agent_version=agent_version,
            ssh_user=agent.ssh_user,
            sudo_password=payload.sudo_password,
        )
        payload_files = {
            "timelapse_agent.py": REPO_ROOT / "agent" / "timelapse_agent.py",
            "timelapse-agent.service": REPO_ROOT
            / "agent"
            / "systemd"
            / "timelapse-agent.service",
        }
        run_provision(
            target=target,
            ssh_user=agent.ssh_user,
            private_key_path=private_key_path,
            known_hosts_path=known_hosts_path,
            install_script=install_script,
            payload_files=payload_files,
        )
    except ProvisionError as error:
        store.update_status(agent_id, status="failed", last_provision_error=str(error))
        raise HTTPException(status_code=502, detail=str(error)) from error

    store.update_status(agent_id, status="provisioned", last_provision_error=None)
    return agent_to_response(store.get(agent_id), include_public_key=False)


@app.get("/api/cameras")
def list_cameras() -> Dict[str, Any]:
    store = load_store()
    cameras = store.setdefault("cameras", {})
    cameras_payload = [
        camera_summary(camera_id, config)
        for camera_id, config in sorted(cameras.items())
    ]
    return {"cameras": cameras_payload, "stats": compute_stats()}


@app.get("/api/cameras/{camera_id}/config")
def read_config(
    camera_id: str,
) -> CameraConfig:
    return get_camera_config(camera_id)


@app.put("/api/cameras/{camera_id}/config")
def update_config(
    camera_id: str,
    config: CameraConfig,
) -> CameraConfig:
    return set_camera_config(camera_id, config)


@app.post("/api/cameras/{camera_id}/checkin")
def post_checkin(
    camera_id: str,
    payload: CheckinRequest,
    request: Request,
) -> Dict[str, Any]:
    camera_id = safe_identifier(camera_id)
    store = load_store()
    cameras = store.setdefault("cameras", {})
    record = cameras.get(camera_id) or model_dict(CameraRecord())

    status = record.setdefault("status", model_dict(CameraStatus()))
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    status["last_seen"] = now_iso
    status["source_ip"] = request.client.host if request.client else None
    if payload.agent_version is not None:
        status["agent_version"] = payload.agent_version
    if payload.hostname is not None:
        status["hostname"] = payload.hostname
    if payload.last_capture_at is not None:
        status["last_capture_at"] = payload.last_capture_at
    if payload.last_upload_at is not None:
        status["last_upload_at"] = payload.last_upload_at
    if payload.pending_count is not None:
        status["pending_count"] = payload.pending_count
    if payload.pending_bytes is not None:
        status["pending_bytes"] = payload.pending_bytes
    if payload.in_schedule is not None:
        status["in_schedule"] = payload.in_schedule
    if payload.local_hour is not None:
        status["local_hour"] = payload.local_hour
    if payload.current_light is not None:
        status["current_light"] = payload.current_light
    if payload.signal_dbm is not None:
        status["signal_dbm"] = payload.signal_dbm
    if payload.active_backend is not None:
        status["active_backend"] = payload.active_backend
    if payload.dslr is not None:
        status["dslr"] = payload.dslr.model_dump()
    status["last_error"] = payload.last_error

    cameras[camera_id] = record
    save_store(store)

    try:
        agent = agent_store().get(camera_id)
    except KeyError:
        agent = None
    if agent is not None and agent.status == "provisioned":
        from app.agents import KeyArchive

        KeyArchive(DATA_DIR).archive_private_key(camera_id)

    return {"acknowledged": True, "last_seen": now_iso}


@app.delete("/api/cameras/{camera_id}", status_code=204)
def delete_camera(camera_id: str) -> None:
    camera_id = safe_identifier(camera_id)
    store = load_store()
    cameras = store.setdefault("cameras", {})
    cameras.pop(camera_id, None)
    save_store(store)

    # Remove agent record + on-disk SSH key material so the camera_id can be recreated cleanly.
    try:
        agent_store().delete(camera_id)
    except Exception:
        pass
    agent_dir = DATA_DIR / "agents" / camera_id
    if agent_dir.exists():
        shutil.rmtree(agent_dir, ignore_errors=True)

    for sub in ("images", "thumbnails", "videos"):
        path = DATA_DIR / sub / camera_id
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
    return None


RELEASES_DIR_NAME = "releases"


def release_paths(version: str) -> Tuple[Path, Path]:
    if not re.match(r"^[A-Za-z0-9._-]+$", version):
        raise HTTPException(status_code=400, detail="Invalid version")
    bundle = DATA_DIR / RELEASES_DIR_NAME / f"timelapse-agent-{version}.tar.gz"
    sha = bundle.with_suffix(bundle.suffix + ".sha256")
    return bundle, sha


@app.get("/api/cameras/{camera_id}/update-manifest")
def get_update_manifest(camera_id: str, request: Request) -> Dict[str, Any]:
    camera_id = safe_identifier(camera_id)
    config = get_camera_config(camera_id)
    desired = config.desired_agent_version
    if not desired:
        raise HTTPException(
            status_code=404, detail="No desired agent version configured"
        )
    bundle, sha = release_paths(desired)
    if not bundle.exists() or not sha.exists():
        raise HTTPException(
            status_code=503, detail=f"Release {desired} not staged on server"
        )
    base_url = resolve_public_server_url(str(request.base_url).rstrip("/"))
    return {
        "version": desired,
        "url": f"{base_url}/api/releases/timelapse-agent-{desired}.tar.gz",
        "sha256": sha.read_text(encoding="utf-8").strip(),
    }


RELEASE_FILENAME_RE = re.compile(r"^timelapse-agent-[A-Za-z0-9._-]+\.tar\.gz$")


@app.get("/api/releases/{filename}")
def serve_release(filename: str) -> FileResponse:
    if not RELEASE_FILENAME_RE.match(filename):
        raise HTTPException(status_code=400, detail="Invalid release filename")
    path = (DATA_DIR / RELEASES_DIR_NAME / filename).resolve()
    releases_root = (DATA_DIR / RELEASES_DIR_NAME).resolve()
    if not str(path).startswith(str(releases_root) + os.sep) and path != releases_root:
        raise HTTPException(status_code=400, detail="Invalid path")
    if not path.exists():
        raise HTTPException(status_code=404, detail="Release not found")
    return FileResponse(path, media_type="application/gzip", filename=filename)


@app.post("/api/cameras/{camera_id}/upload")
async def upload_image(
    camera_id: str,
    background_tasks: BackgroundTasks,
    image: UploadFile = File(...),
    x_captured_at: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    camera_id = safe_identifier(camera_id)
    captured_at = parse_capture_time(x_captured_at)
    destination = image_path(camera_id, captured_at)

    with destination.open("wb") as output_file:
        shutil.copyfileobj(image.file, output_file)

    background_tasks.add_task(
        ensure_frame_thumbnail,
        destination,
        thumbnail_path(camera_id, destination.parent.name, destination.name),
    )

    return {
        "stored": True,
        "camera_id": camera_id,
        "path": str(destination.relative_to(DATA_DIR)),
    }


@app.get("/api/cameras/{camera_id}/latest")
def read_latest_image(
    camera_id: str,
) -> FileResponse:
    camera_id = safe_identifier(camera_id)
    latest = latest_image(camera_id)
    if not latest:
        raise HTTPException(status_code=404, detail="No images uploaded yet")
    return FileResponse(latest, media_type="image/jpeg")


@app.get("/api/cameras/{camera_id}/frame-days")
def list_frame_days(camera_id: str) -> Dict[str, Any]:
    camera_id = safe_identifier(camera_id)
    by_day: Dict[str, List[Path]] = {}
    for path in list_valid_camera_frames(camera_id):
        by_day.setdefault(path.parent.name, []).append(path)

    days = [
        frame_day_response(camera_id, day, paths)
        for day, paths in sorted(by_day.items(), reverse=True)
    ]
    month_totals: Dict[str, Dict[str, Any]] = {}
    for day in days:
        month = day["month"]
        month_record = month_totals.setdefault(
            month,
            {"month": month, "day_count": 0, "frame_count": 0, "gap_count": 0},
        )
        month_record["day_count"] += 1
        month_record["frame_count"] += day["count"]
        month_record["gap_count"] += day["gap_count"]

    months = [
        month_totals[month] for month in sorted(month_totals.keys(), reverse=True)
    ]
    return {
        "days": days,
        "months": months,
        "total_days": len(days),
        "total_frames": sum(day["count"] for day in days),
    }


@app.get("/api/cameras/{camera_id}/frames")
def list_frames(
    camera_id: str,
    day: Optional[str] = None,
    before: Optional[str] = None,
    limit: int = Query(60, ge=1, le=5000),
    order: str = Query("desc", pattern="^(asc|desc)$"),
) -> Dict[str, Any]:
    camera_id = safe_identifier(camera_id)
    if day is not None:
        day = validate_frame_day(day)
    if before is not None:
        try:
            before_day, before_filename = before.split("/", 1)
        except ValueError as error:
            raise HTTPException(
                status_code=400, detail="Invalid frame cursor"
            ) from error
        validate_frame_day(before_day)
        validate_frame_filename(before_filename)

    frames = list_valid_camera_frames(camera_id)
    if day is not None:
        frames = [path for path in frames if path.parent.name == day]
    frames = sorted(frames, key=frame_key, reverse=(order == "desc"))
    total = len(frames)
    if before is not None:
        frames = [path for path in frames if frame_key(path) < before]

    page = frames[:limit]
    has_more = len(frames) > limit
    return {
        "frames": [frame_to_response(camera_id, path) for path in page],
        "total": total,
        "returned": len(page),
        "has_more": has_more,
        "next_cursor": frame_key(page[-1]) if has_more and page else None,
    }


@app.get("/api/cameras/{camera_id}/frames/{day}/{filename}")
def read_frame(
    camera_id: str,
    day: str,
    filename: str,
) -> FileResponse:
    camera_id = safe_identifier(camera_id)
    day = validate_frame_day(day)
    filename = validate_frame_filename(filename)
    path = DATA_DIR / "images" / camera_id / day / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Frame not found")
    return FileResponse(path, media_type="image/jpeg", filename=filename)


@app.get("/api/cameras/{camera_id}/frames/{day}/{filename}/thumbnail")
def read_frame_thumbnail(
    camera_id: str,
    day: str,
    filename: str,
) -> FileResponse:
    camera_id = safe_identifier(camera_id)
    day = validate_frame_day(day)
    filename = validate_frame_filename(filename)
    source = DATA_DIR / "images" / camera_id / day / filename
    if not source.exists() or not source.is_file():
        raise HTTPException(status_code=404, detail="Frame not found")

    thumbnail = thumbnail_path(camera_id, day, filename)
    if ensure_frame_thumbnail(source, thumbnail):
        return FileResponse(thumbnail, media_type="image/jpeg")
    return FileResponse(source, media_type="image/jpeg")


@app.delete("/api/cameras/{camera_id}/frames/{day}/{filename}", status_code=204)
def delete_frame(
    camera_id: str,
    day: str,
    filename: str,
) -> None:
    camera_id = safe_identifier(camera_id)
    day = validate_frame_day(day)
    filename = validate_frame_filename(filename)
    path = DATA_DIR / "images" / camera_id / day / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Frame not found")
    path.unlink(missing_ok=True)
    delete_frame_thumbnail(camera_id, day, filename)
    return None


@app.post("/api/cameras/{camera_id}/videos")
async def generate_video(
    camera_id: str,
    request: VideoRequest,
    http_request: Request,
) -> StreamingResponse:
    camera_id = safe_identifier(camera_id)
    images = selected_images(camera_id, request)
    if not images:
        raise HTTPException(status_code=404, detail="No images found for selection")

    if not shutil.which("ffmpeg"):
        raise HTTPException(status_code=500, detail="ffmpeg is not installed")

    video_dir = DATA_DIR / "videos" / camera_id
    video_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if request.name:
        try:
            requested_name = safe_identifier(request.name)
        except HTTPException as error:
            raise HTTPException(
                status_code=400, detail=f"Invalid video name: {error.detail}"
            ) from error
    else:
        requested_name = f"timelapse-{timestamp}"

    output_path = video_dir / f"{requested_name}.{request.format}"
    list_path = video_dir / f"{requested_name}.txt"

    with list_path.open("w", encoding="utf-8") as list_file:
        for path in images:
            list_file.write(f"file '{ffmpeg_escape(path)}'\n")

    async def event_stream():
        total_frames = len(images)
        try:
            if request.format == "mp4":
                command = [
                    "ffmpeg",
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(list_path),
                    "-vf",
                    f"fps={request.fps},format=yuv420p",
                    "-c:v",
                    "libx264",
                    "-movflags",
                    "+faststart",
                    "-progress",
                    "pipe:1",
                    str(output_path),
                ]
                process = await asyncio.create_subprocess_exec(
                    *command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                last_frame = 0
                try:
                    while True:
                        if await http_request.is_disconnected():
                            process.terminate()
                            await process.wait()
                            return
                        line = await process.stdout.readline()
                        if not line:
                            break
                        text = line.decode("utf-8", errors="replace").strip()
                        if not text or "=" not in text:
                            continue
                        key, value = text.split("=", 1)
                        if key == "frame":
                            try:
                                last_frame = int(value)
                            except ValueError:
                                continue
                        elif key == "progress":
                            percent = (
                                min(100, round(last_frame / total_frames * 100))
                                if total_frames
                                else 0
                            )
                            yield f"event: progress\ndata: {json.dumps({'frame': last_frame, 'total': total_frames, 'percent': percent})}\n\n"
                            if value == "end":
                                break
                    return_code = await process.wait()
                    if return_code != 0:
                        stderr = (
                            (await process.stderr.read())
                            .decode("utf-8", errors="replace")
                            .strip()
                        )
                        yield f"event: error\ndata: {json.dumps({'detail': stderr or 'ffmpeg failed'})}\n\n"
                        return
                finally:
                    if process.returncode is None:
                        process.terminate()
                        await process.wait()
            else:
                loop = asyncio.get_running_loop()
                try:
                    await loop.run_in_executor(
                        None,
                        run_ffmpeg_gif,
                        list_path,
                        output_path,
                        request.fps,
                        video_dir,
                        requested_name,
                    )
                except HTTPException as exc:
                    yield f"event: error\ndata: {json.dumps({'detail': exc.detail})}\n\n"
                    return

            yield f"event: done\ndata: {json.dumps({'path': str(output_path.relative_to(DATA_DIR))})}\n\n"
        finally:
            list_path.unlink(missing_ok=True)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def run_ffmpeg_mp4(list_path: Path, output_path: Path, fps: int) -> None:
    command = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_path),
        "-vf",
        f"fps={fps},format=yuv420p",
        "-c:v",
        "libx264",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as error:
        raise HTTPException(status_code=500, detail=error.stderr.strip()) from error


def run_ffmpeg_gif(
    list_path: Path, output_path: Path, fps: int, work_dir: Path, base_name: str
) -> None:
    palette_path = work_dir / f"{base_name}-palette.png"
    palette_command = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_path),
        "-vf",
        f"fps={fps},scale=720:-1:flags=lanczos,palettegen",
        str(palette_path),
    ]
    encode_command = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_path),
        "-i",
        str(palette_path),
        "-filter_complex",
        f"fps={fps},scale=720:-1:flags=lanczos[x];[x][1:v]paletteuse",
        str(output_path),
    ]
    try:
        subprocess.run(palette_command, check=True, capture_output=True, text=True)
        subprocess.run(encode_command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as error:
        raise HTTPException(status_code=500, detail=error.stderr.strip()) from error
    finally:
        palette_path.unlink(missing_ok=True)


SUPPORTED_VIDEO_FORMATS = {".mp4": "video/mp4", ".gif": "image/gif"}


@app.get("/api/cameras/{camera_id}/videos")
def list_videos(camera_id: str) -> Dict[str, Any]:
    camera_id = safe_identifier(camera_id)
    video_dir = DATA_DIR / "videos" / camera_id
    if not video_dir.exists():
        return {"videos": []}
    items = []
    for entry in video_dir.iterdir():
        if not entry.is_file():
            continue
        suffix = entry.suffix.lower()
        if suffix not in (".mp4", ".gif"):
            continue
        try:
            stat = entry.stat()
        except OSError:
            continue
        items.append(
            {
                "filename": entry.name,
                "size_bytes": stat.st_size,
                "format": suffix.lstrip("."),
                "created_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
            }
        )
    items.sort(key=lambda v: v["created_at"], reverse=True)
    return {"videos": items}


@app.get("/api/cameras/{camera_id}/videos/{filename}")
def read_video(
    camera_id: str,
    filename: str,
) -> FileResponse:
    camera_id = safe_identifier(camera_id)
    filename = safe_identifier(filename)
    path = DATA_DIR / "videos" / camera_id / filename
    media_type = SUPPORTED_VIDEO_FORMATS.get(path.suffix)
    if media_type is None or not path.exists():
        raise HTTPException(status_code=404, detail="Video not found")
    return FileResponse(path, media_type=media_type)


@app.delete("/api/cameras/{camera_id}/videos/{filename}", status_code=204)
def delete_video(camera_id: str, filename: str) -> None:
    camera_id = safe_identifier(camera_id)
    filename = safe_identifier(filename)
    path = DATA_DIR / "videos" / camera_id / filename
    if path.suffix not in SUPPORTED_VIDEO_FORMATS or not path.exists():
        raise HTTPException(status_code=404, detail="Video not found")
    path.unlink(missing_ok=True)
    return None


@app.api_route(
    "/api/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    include_in_schema=False,
)
def api_not_found(path: str) -> None:
    raise HTTPException(status_code=404)


@app.get("/{path:path}", include_in_schema=False)
def spa_fallback(path: str) -> FileResponse:
    if path.startswith("api/") or path.startswith("static/"):
        raise HTTPException(status_code=404)
    return FileResponse(STATIC_DIR / "v2" / "index.html", media_type="text/html")
