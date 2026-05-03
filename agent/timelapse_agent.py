#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


AGENT_VERSION = "0.3.0"


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


def post_checkin(settings: Dict[str, Any], state: AgentState) -> None:
    url = settings["server_url"].rstrip("/") + f"/api/cameras/{settings['camera_id']}/checkin"
    payload = {
        "agent_version": AGENT_VERSION,
        "hostname": socket.gethostname(),
        "last_capture_at": state.last_capture_at,
        "last_upload_at": state.last_upload_at,
        "last_error": state.last_error,
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


def capture_frame(work_dir: Path, config: Dict[str, Any]) -> Path:
    command = find_capture_command()
    if not command:
        raise RuntimeError("No camera command found: expected rpicam-still, libcamera-still, or raspistill")

    captured_at = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_path = work_dir / "pending" / f"{captured_at}.jpg"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_suffix(".tmp.jpg")

    subprocess.run(build_capture_command(command, temp_path, config), check=True)
    temp_path.replace(output_path)

    metadata = {
        "captured_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
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
    pending_dir = work_dir / "pending"
    pending_dir.mkdir(parents=True, exist_ok=True)
    url = settings["server_url"].rstrip("/") + f"/api/cameras/{settings['camera_id']}/upload"

    for image_path in sorted(pending_dir.glob("*.jpg")):
        metadata_path = image_path.with_suffix(".json")
        metadata = load_json(metadata_path) if metadata_path.exists() else {}
        captured_at = metadata.get(
            "captured_at",
            datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        )

        try:
            post_multipart(url, image_path, captured_at)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
            logging.warning("Upload failed for %s: %s", image_path.name, error)
            state.last_error = f"upload failed: {error}"
            return

        image_path.unlink(missing_ok=True)
        metadata_path.unlink(missing_ok=True)
        state.last_upload_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        state.last_error = None
        logging.info("Uploaded %s", image_path.name)


def next_due_time(last_capture: Optional[float], interval_seconds: int, now: float) -> float:
    if last_capture is None:
        return now
    return max(now, last_capture + interval_seconds)


def run_agent(settings: Dict[str, Any]) -> None:
    work_dir = Path(settings.get("work_dir", "/var/lib/timelapse-agent"))
    work_dir.mkdir(parents=True, exist_ok=True)
    cache_path = work_dir / "server-config.json"

    poll_seconds = int(settings.get("config_poll_seconds", 60))
    state = AgentState()
    remote_config = fetch_remote_config(settings, cache_path)
    last_capture: Optional[float] = None
    next_capture = time.monotonic()
    next_config_poll = time.monotonic() + poll_seconds

    logging.info("Agent v%s started for camera_id=%s", AGENT_VERSION, settings["camera_id"])
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
            post_checkin(settings, state)
            next_config_poll = now + poll_seconds

        upload_pending(settings, work_dir, state)

        enabled = bool(remote_config.get("enabled", True))
        interval_seconds = int(remote_config.get("interval_seconds", 900))
        if enabled and now >= next_capture:
            try:
                image_path = capture_frame(work_dir, remote_config)
                state.last_capture_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                state.last_error = None
                logging.info("Captured %s", image_path.name)
            except Exception as error:
                state.last_error = str(error)
                logging.exception("Capture failed")
                next_capture = now + min(300, interval_seconds)
            else:
                last_capture = time.monotonic()
                upload_pending(settings, work_dir, state)
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
