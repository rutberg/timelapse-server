from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from ipaddress import ip_address, ip_network
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

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
DEFAULT_ALLOWED_NETWORKS = (
    "127.0.0.0/8,"
    "10.0.0.0/8,"
    "172.16.0.0/12,"
    "192.168.0.0/16,"
    "::1/128,"
    "fc00::/7,"
    "fe80::/10"
)
ALLOWED_NETWORKS = [
    ip_network(value.strip())
    for value in os.environ.get("TIMELAPSE_ALLOWED_NETWORKS", DEFAULT_ALLOWED_NETWORKS).split(",")
    if value.strip()
]


class CameraConfig(BaseModel):
    enabled: bool = True
    interval_seconds: int = Field(900, ge=30, le=86_400)
    image_width: Optional[int] = Field(None, ge=320, le=10_000)
    image_height: Optional[int] = Field(None, ge=240, le=10_000)
    jpeg_quality: int = Field(85, ge=1, le=100)


class CameraStatus(BaseModel):
    hostname: Optional[str] = None
    source_ip: Optional[str] = None
    last_seen: Optional[str] = None
    last_capture_at: Optional[str] = None
    last_upload_at: Optional[str] = None
    last_error: Optional[str] = None
    agent_version: Optional[str] = None


class CameraRecord(BaseModel):
    config: CameraConfig = Field(default_factory=CameraConfig)
    status: CameraStatus = Field(default_factory=CameraStatus)


class CheckinRequest(BaseModel):
    agent_version: Optional[str] = None
    hostname: Optional[str] = None
    last_capture_at: Optional[str] = None
    last_upload_at: Optional[str] = None
    last_error: Optional[str] = None


HOSTNAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9.-]{0,253}$")


class CreateAgentRequest(BaseModel):
    agent_id: str
    display_name: str
    expected_hostname: str
    ip_fallback: Optional[str] = None
    ssh_user: str = "pi"


class ProvisionRequest(BaseModel):
    ip_fallback: Optional[str] = None


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


def model_dict(model: BaseModel) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


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
    (DATA_DIR / "videos").mkdir(exist_ok=True)


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
    if not value:
        return datetime.now(timezone.utc)
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def image_path(camera_id: str, captured_at: datetime) -> Path:
    day = captured_at.strftime("%Y-%m-%d")
    timestamp = captured_at.strftime("%Y%m%dT%H%M%SZ")
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


ONLINE_GRACE_SECONDS = 300


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
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")


@app.get("/api/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


def agent_store() -> AgentStore:
    return AgentStore(DATA_DIR)


def agent_to_response(agent: PendingAgent, include_public_key: bool = False) -> Dict[str, Any]:
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
def provision_agent(agent_id: str, payload: ProvisionRequest, request: Request) -> Dict[str, Any]:
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

    server_url = str(request.base_url).rstrip("/")
    agent_dir = DATA_DIR / "agents" / agent_id
    private_key_path = agent_dir / "id_ed25519"
    known_hosts_path = agent_dir / "known_hosts"

    try:
        target = resolve_target(agent.expected_hostname, agent.ip_fallback)
        from timelapse_agent import AGENT_VERSION  # noqa: WPS433
        install_script = build_install_script(
            camera_id=agent_id,
            server_url=server_url,
            agent_version=AGENT_VERSION,
        )
        payload_files = {
            "timelapse_agent.py": REPO_ROOT / "agent" / "timelapse_agent.py",
            "timelapse-agent.service": REPO_ROOT / "agent" / "systemd" / "timelapse-agent.service",
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
    return {
        "cameras": [
            camera_summary(camera_id, config)
            for camera_id, config in sorted(cameras.items())
        ]
    }


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


@app.post("/api/cameras/{camera_id}/upload")
async def upload_image(
    camera_id: str,
    image: UploadFile = File(...),
    x_captured_at: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    camera_id = safe_identifier(camera_id)
    captured_at = parse_capture_time(x_captured_at)
    destination = image_path(camera_id, captured_at)

    with destination.open("wb") as output_file:
        shutil.copyfileobj(image.file, output_file)

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


@app.post("/api/cameras/{camera_id}/videos")
def generate_video(
    camera_id: str,
    request: VideoRequest,
) -> Dict[str, Any]:
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
            raise HTTPException(status_code=400, detail=f"Invalid video name: {error.detail}") from error
    else:
        requested_name = f"timelapse-{timestamp}"
    output_path = video_dir / f"{requested_name}.mp4"
    list_path = video_dir / f"{requested_name}.txt"

    with list_path.open("w", encoding="utf-8") as list_file:
        for path in images:
            list_file.write(f"file '{ffmpeg_escape(path)}'\n")

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
        str(output_path),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as error:
        raise HTTPException(status_code=500, detail=error.stderr.strip()) from error
    finally:
        list_path.unlink(missing_ok=True)

    return {
        "generated": True,
        "camera_id": camera_id,
        "image_count": len(images),
        "path": str(output_path.relative_to(DATA_DIR)),
    }


@app.get("/api/cameras/{camera_id}/videos/{filename}")
def read_video(
    camera_id: str,
    filename: str,
) -> FileResponse:
    camera_id = safe_identifier(camera_id)
    filename = safe_identifier(filename)
    path = DATA_DIR / "videos" / camera_id / filename
    if path.suffix != ".mp4" or not path.exists():
        raise HTTPException(status_code=404, detail="Video not found")
    return FileResponse(path, media_type="video/mp4")


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
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")
