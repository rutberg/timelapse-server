<img width="1040" height="833" alt="image" src="https://github.com/user-attachments/assets/e7b703fe-f69a-4a24-b22d-0fce6ddfd2bd" />

# Timelapse System

A lightweight, server-controlled timelapse system for Raspberry Pi, supporting both the Raspberry Pi Camera Module and USB-tethered DSLRs.

## Overview

The system is designed for local network operations where the capture device (agent) stays simple and secure:

1.  **Server-Controlled:** The Pi agent polls a central server for its configuration.
2.  **Simple Agent:** No public IP, inbound SSH, or complex auth required on the Pi. It just needs outbound access to the server.
3.  **Configurable:** The server dictates capture intervals, image resolution, and quality.
4.  **Automated Upload:** Captured images are immediately uploaded to the server for storage and processing.
5.  **Video Generation:** The server can compile captured images into MP4 videos using `ffmpeg`.

## Project Structure

- `server/` - FastAPI backend for configuration management, image uploads, and video generation.
- `agent/` - Python-based capture agent for the Raspberry Pi.
- `scripts/` - Automated installation and update scripts for the server.

---

## Server Setup

The server is designed to run on a Debian-based system (e.g., a Debian LXC, Ubuntu VM, or a dedicated server).

### Web UI

After install, browse to `http://<SERVER_IP>:<PORT>/` from any LAN host. The UI provides:

- **Cameras** - auto-refreshing status tiles for every camera that has checked in.
- **Add agent** - three-step wizard (form -> flash with Imager -> SSH provision) backed by `/api/agents`.
- **Camera detail** - capture settings, latest image preview, video render button, and `desired_agent_version` for rolling out updates. You can also **feature** specific cameras to pin them to the top of the dashboard and add **location labels** for easier identification.

The UI is served from `/static/` and uses Alpine.js + pico.css. Both are vendored, so the LXC works offline.

### Automated Installation

On a fresh Debian installation, you can install the server with a single command:

```bash
curl -fsSL https://raw.githubusercontent.com/rutberg/timelapse-server/main/scripts/install-server.sh \
  | sudo env TIMELAPSE_REPO_URL=https://github.com/rutberg/timelapse-server bash
```

The installer will:
- Clone/update the repository to `/opt/timelapse`.
- Install necessary dependencies (`git`, `python3-venv`, `ffmpeg`).
- Create a data directory at `/srv/timelapse` for images and videos.
- Auto-detect the local IP and LAN CIDR for security.
- Configure and start the `timelapse-server` systemd service.

### Manual Configuration

Configuration is stored in `/etc/timelapse-server/server.env`. You can customize the binding host, port, and allowed networks:

```bash
TIMELAPSE_DATA_DIR=/srv/timelapse
TIMELAPSE_ALLOWED_NETWORKS=127.0.0.0/8,192.168.0.0/16
TIMELAPSE_BIND_HOST=0.0.0.0
TIMELAPSE_PORT=8080
```

Restart the service after changes:
```bash
sudo systemctl restart timelapse-server
```

---

## Agent Setup

The system uses a web-based wizard to provision new Raspberry Pi agents. Access it by clicking **Add Agent** in the Web UI.

### 1. Define the Agent
Fill out the form in the wizard:
- **Agent ID**: A unique identifier (e.g., `tomatoes-zero-w`).
- **Display Name**: A friendly name for the UI.
- **Expected Hostname**: The hostname the Pi will use on your network (e.g., `timelapse-tomatoes`).
- **SSH User**: The user created during flashing (usually `pi`).

Click **Next** to generate the SSH public key. Copy this key for the next step.

### 2. Flash the SD card with Raspberry Pi Imager
In Raspberry Pi Imager, choose **Raspberry Pi OS Lite** and open the OS-customisation panel (the gear icon). Set:

- **Hostname**: the same value you used for `Expected Hostname`.
- **SSH**: enabled, **using the public key** you copied from the wizard.
- **Wi-Fi**: SSID, password, and country.

Flash, insert the SD card, power the Pi on, and wait one minute for the first boot.

### 3. Provision
In the wizard, click **Provision Agent**.

The server will attempt to connect to the Pi at `<hostname>.local`. If your network doesn't support mDNS, you can provide the IP address manually in the wizard.

Once provisioning is complete, the agent will install itself as a systemd service and post its first heartbeat within 60 seconds. The camera will then appear on the Dashboard.

---

## Usage & API

### Camera Status

Each agent posts a heartbeat to `POST /api/cameras/<id>/checkin` on every config-poll tick. The server tracks `last_seen`, `agent_version`, `hostname`, and the most recent capture/upload/error. List all cameras with their current status:

```bash
curl http://<SERVER_IP>:8080/api/cameras
```

A camera is considered online if its last heartbeat was within five minutes (or three poll intervals, whichever is larger).

### Setting Camera Configuration

You can change capture settings at any time via the server API. The agent will pick up changes on its next poll.

```bash
curl -X PUT http://<SERVER_IP>:8080/api/cameras/<CAMERA_ID>/config \
  -H 'Content-Type: application/json' \
  -d '{
    "enabled": true,
    "interval_seconds": 600,
    "image_width": 1920,
    "image_height": 1080,
    "jpeg_quality": 85
  }'
```

### Scheduling modes

Each camera supports three capture-schedule modes via the `schedule_mode` config field:

- **`hours`** — fixed start/end window in agent local time. The UI uses two hour inputs and a visual track; on the wire `capture_hours = [start..end-1]`.
- **`daylight`** — auto-derived from sunrise/sunset for the camera's `latitude`/`longitude` (NOAA approximation, hour resolution). Falls back to 06:00–20:00 when location is unset.
- **`scene`** — capture only when the scene is bright enough. Configured by `light_threshold` (mean Y luminance, 0–255). Before each scheduled capture the agent samples a 64×48 YUV thumbnail; if mean Y is below the threshold the frame is skipped. The latest reading is reported as `current_light` in heartbeats and shown live in the UI.

A weekday gate (`schedule_days`, ISO weekdays 1=Mon..7=Sun, `null` = every day) applies to all three modes.

### Generating Video

Trigger video generation for a specific camera:

```bash
curl -X POST http://<SERVER_IP>:8080/api/cameras/<CAMERA_ID>/videos \
  -H 'Content-Type: application/json' \
  -d '{
    "fps": 24,
    "format": "mp4",
    "range_preset": "7d"
  }'
```

Supported options:
- **`format`**: `mp4` (default) or `gif`.
- **`fps`**: Frames per second (1–60).
- **`range_preset`**: `24h` (last 24 hours), `7d` (last 7 days), or `all` (everything).
- **`start_date`** / **`end_date`**: Custom range in `YYYY-MM-DD` format.

### Releasing a new agent version

1. Bump `agent/VERSION`.
2. Build the bundle:
   ```bash
   scripts/build-release.sh --out /srv/timelapse/releases
   ```
   The release directory is `${TIMELAPSE_DATA_DIR}/releases`. On a default install that's `/srv/timelapse/releases`. Building into the wrong directory is a common cause of `Release X not staged on server` from the manifest endpoint.
3. Set the desired version on each camera:
   ```bash
   curl -X PUT http://<SERVER_IP>:8080/api/cameras/<CAMERA_ID>/config \
     -H 'Content-Type: application/json' \
     -d '{
       "enabled": true,
       "interval_seconds": 600,
       "image_width": null,
       "image_height": null,
       "jpeg_quality": 85,
       "desired_agent_version": "0.3.1"
     }'
   ```
4. Within one poll cycle (default 60s) the agent downloads, verifies, installs, and restarts on the new version. The next heartbeat reports the new `agent_version`.

If the install fails, the agent logs `Update failed: ...` and stays on the old version. Watch with `journalctl -u timelapse-agent -f` on the Pi during rollouts.

**When the change spans agent + server.** The agent ships in a tarball and updates itself on the next poll, but the server (FastAPI / Pydantic models) only picks up edits when uvicorn restarts. If a release adds or renames fields on the checkin payload (e.g. `DslrStatus.lens_name`, `DslrStatus.current_values`), restart the server *before* the new agent first checks in — Pydantic silently drops unknown fields by default, so the data appears to flow but never lands.

```bash
sudo systemctl restart timelapse-server
```

---

## DSLR cameras (gphoto2 backend)

The agent supports two capture backends: `rpicam` for Pi Camera modules and `gphoto2` for USB-tethered DSLRs. The active backend is auto-detected unless `CameraConfig.camera_backend` is pinned.

### Dynamic Property Mapping

Unlike the Raspberry Pi camera which has a fixed set of controls, every DSLR model exposes a different set of keys and values via `gphoto2`. The system uses a **Property Map** to bridge these differences:

1.  **Vendor Support:** Tailored support for Canon, Nikon, Sony, Fuji, Olympus, and Panasonic bodies.
2.  **Telemetry Tiles:** Dynamically rendered UI tiles for battery level, shutter count, lens name, and available shots, mapped to the correct vendor-specific PTP keys.
3.  **Capture Settings:** Mapped dropdowns for Shutter Speed, Aperture, ISO, and White Balance. It handles cases where read and write keys differ (e.g., Nikon's `shutterspeed` vs `shutterspeed2`).
4.  **Initialization Keys:** Critical startup settings like `capturetarget` (SD card vs Internal) and `focusmode` are managed via the Property Map.

### DSLR Discovery

To support a new or unknown camera body, the UI provides a **Re-run discovery** feature:

1.  The server issues a discovery token to the agent.
2.  The agent runs `gphoto2 --list-all-config` to dump every property the camera supports.
3.  The agent posts this raw tree back to the server.
4.  The server parses the tree and proposes a new Property Map based on a flat priority list of known vendor keys.
5.  Once confirmed in the UI, the map is saved to the camera's configuration and used for all subsequent UI rendering and capture commands.

### Provisioning for USB Cameras

The automated provisioning process (`Provision Agent` in the UI) automatically handles the extra complexity of USB-tethered cameras:
- **Dependencies:** Installs `gphoto2` and the `libgphoto2` runtime.
- **Udev Rules:** Writes a custom udev rule (`06-still-image.rules`) to grant the `pi` user permission to access any USB Still Image device (Class 06).
- **Process Locking:** Masks the `gvfs-gphoto2-volume-monitor` systemd service to prevent the OS from auto-mounting the camera and locking out the capture agent.

### Testing without hardware

`scripts/simulate-dslr.sh` can simulate the full DSLR lifecycle, including checkins, telemetry updates, and discovery:

```bash
# Simulate a Canon R6 on a camera named 'dslr-test'
./scripts/simulate-dslr.sh dslr-test http://127.0.0.1:8080 30
```

The simulator posts realistic fixtures that exercise the Property Map rendering and the re-initialization flow.


---

## Security Note

This project is designed for use on a trusted LAN — typically a home or studio network where every device on-link is already trusted. It is **not** hardened for direct exposure to the public internet.

### Threat model

- **No built-in authentication.** The HTTP API and web UI have no login, session, or per-user access control. Any client that can reach the bind address can capture, configure cameras, or trigger provisioning. Access control is delegated entirely to the network layer via `TIMELAPSE_ALLOWED_NETWORKS` (a CIDR allowlist enforced as middleware) and `TIMELAPSE_BIND_HOST`.
- **LAN-only deployment.** The defaults bind to LAN interfaces and reject requests outside the configured CIDR. Do not port-forward the server, place it behind a public reverse proxy, or expose it via tunnel without adding your own authentication layer (e.g. a reverse proxy with HTTP basic auth, mTLS, Tailscale, or a VPN).
- **Agents trust the server.** Pi agents pull configuration and (optionally) self-update from the server they are paired with. A compromised or impersonated server can push arbitrary commands or code to every paired agent. Keep the server host trustworthy and the LAN segment closed.
- **First-time SSH provisioning uses `StrictHostKeyChecking=accept-new`.** When you provision a fresh Pi from the web UI, the server connects over SSH and trusts the host key on first contact (TOFU). This is convenient on a known-good LAN but means a man-in-the-middle on the LAN during initial provisioning could substitute their own host key. Provision agents on a network you control; subsequent connections verify the pinned key.
- **Sudo password handling.** Provisioning may collect a sudo password to install the agent on the Pi. It is held in memory for the duration of the SSH session and is not persisted to disk. Reset/rotate after provisioning if your threat model requires it.

### Recommended hardening

- Keep `TIMELAPSE_ALLOWED_NETWORKS` as narrow as possible (specific CIDRs, not `0.0.0.0/0`).
- Bind to a specific interface (`TIMELAPSE_BIND_HOST=192.168.x.y`) rather than `0.0.0.0` if multiple networks are reachable.
- If remote access is needed, front the server with a reverse proxy that adds authentication, or reach it over Tailscale/WireGuard rather than opening a public port.
- Provision agents over a quiet, trusted LAN segment.

If you find a security issue, please follow the disclosure process in [SECURITY.md](SECURITY.md).

---

## Development

### Environment setup

Run `scripts/codex-setup.sh` in cloud/CI environments. It creates `.venv-dev`, installs `requirements-dev.txt`, and tries to install the system packages needed by video rendering and provisioning code. If `apt-get` is blocked the script continues so Python tests can still run.

Useful environment defaults for local development:

```bash
TIMELAPSE_DATA_DIR=/tmp/timelapse-data
TIMELAPSE_ALLOWED_NETWORKS=127.0.0.0/8,::1/128
TIMELAPSE_BIND_HOST=127.0.0.1
TIMELAPSE_PORT=8080
TIMELAPSE_VENV=.venv-dev
```

### Running tests

Run the full test suite before opening a PR:

```bash
.venv-dev/bin/python -m pytest
```

Narrower commands for faster iteration:

```bash
.venv-dev/bin/python -m pytest tests/server   # server only
.venv-dev/bin/python -m pytest tests/agent    # agent only
```

### Development server

```bash
scripts/dev-server.sh start   # http://127.0.0.1:8080
scripts/dev-server.sh stop
```
