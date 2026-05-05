<img width="1040" height="833" alt="image" src="https://github.com/user-attachments/assets/e7b703fe-f69a-4a24-b22d-0fce6ddfd2bd" />

# Timelapse System

A lightweight, server-controlled timelapse system designed for Raspberry Pi Zero W (or other Pi models) with the Raspberry Pi Camera Module.

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
- **Camera detail** - capture settings, latest image preview, video render button, and `desired_agent_version` for rolling out updates.

The UI is served from `/static/` and uses Alpine.js + pico.css. Both are vendored, so the LXC works offline.

### Automated Installation

On a fresh Debian installation, you can install the server with a single command:

```bash
curl -fsSL https://raw.githubusercontent.com/rutberg/timelapse/main/scripts/install-server.sh \
  | sudo env TIMELAPSE_REPO_URL=https://github.com/rutberg/timelapse bash
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

## Agent Setup (Server-Driven)

The server provisions each Raspberry Pi over SSH the first time you connect it. You only use Raspberry Pi Imager to get the Pi online.

### 1. Create a pending agent

```bash
curl -X POST http://<SERVER_IP>:8080/api/agents \
  -H 'Content-Type: application/json' \
  -d '{
    "agent_id": "tomatoes-zero-w",
    "display_name": "Tomato Cam",
    "expected_hostname": "timelapse-tomatoes",
    "ip_fallback": null,
    "ssh_user": "pi"
  }'
```

The response contains a `public_key` line. Copy it.

### 2. Flash the SD card with Raspberry Pi Imager

In Raspberry Pi Imager, choose **Raspberry Pi OS Lite** and open the OS-customisation panel. Set:

- Hostname: the same value you used for `expected_hostname`.
- SSH: enabled, **using the public key** you copied above.
- Wi-Fi SSID, password, and country.

Flash, insert the SD card, power the Pi on, and wait one minute for first boot.

### 3. Provision

```bash
curl -X POST http://<SERVER_IP>:8080/api/agents/tomatoes-zero-w/provision \
  -H 'Content-Type: application/json' \
  -d '{}'
```

If the server cannot reach `timelapse-tomatoes.local`, supply the IP:
```bash
curl -X POST .../provision -H 'Content-Type: application/json' -d '{"ip_fallback":"192.168.1.50"}'
```

After success the agent posts a heartbeat within 60 seconds. List agents:
```bash
curl http://<SERVER_IP>:8080/api/agents
```

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
  -d '{"fps": 24}'
```

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

The agent supports two capture backends: `rpicam` for Pi Camera modules and `gphoto2` for USB-tethered DSLRs (tested with the Canon R6). The active backend is auto-detected unless `CameraConfig.camera_backend` is pinned.

### Data model

When `active_backend == "gphoto2"`, every checkin includes a `dslr` payload populated by `gphoto2_read_choices_and_current()`, `gphoto2_read_current_values()`, and `gphoto2_read_telemetry()` in `agent/timelapse_agent.py`:

| Field | Source | Refreshed |
|---|---|---|
| `battery_level`, `available_shots`, `shutter_counter`, `exposure_mode`, `lens_name` | `gphoto2 --get-config <key>` `Current:` line | every poll |
| `choices` (per-setting option lists) | `gphoto2 --get-config` `Choice:` lines | only on init/reinit |
| `current_values` (per-setting active value) | `gphoto2 --get-config` `Current:` line | every poll |
| `last_reinit_token`, `last_init_at` | agent state | on reinit |

`CameraConfig.dslr` (a `DslrSettings`) holds the user's saved selections plus a `reinit_token`. To trigger a re-initialization, the UI writes a fresh ISO-timestamp into `reinit_token`; the agent compares it against `state.last_reinit_token` on the next poll and, on mismatch, calls `gphoto2_apply_init_settings()` then re-reads choices and current values.

### UI rendering

The DSLR section in the camera Settings tab (`server/app/static/v2/views/camera.js` → `renderDslrSection`) only renders when `cfg.camera_backend === 'gphoto2'` **or** `status.active_backend === 'gphoto2'`. Dropdowns pre-select `current_values[gphotoKey]` first, falling back to the saved `dslrCfg[field]`. The Re-initialize button surfaces a phase tracker (`saving → waiting → done`) that survives page reloads via the camera's `reinit_token` / `last_reinit_token` mismatch.

### Testing without hardware

`scripts/simulate-dslr.sh` posts a realistic Canon R6 fixture (lens name, full `choices`/`current_values`, battery, shutter count) and applies any new `reinit_token` after a 1s delay so the full re-init flow is exercisable. Run it against the dev server and the `dslr-test` camera will show populated dropdowns and the lens line within one tick.

```bash
./scripts/simulate-dslr.sh dslr-test http://127.0.0.1:8080 30
```

---

## Security Note

By default, the server is configured to only allow connections from the local network. Ensure `TIMELAPSE_ALLOWED_NETWORKS` in your server environment file correctly reflects your LAN setup.

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
