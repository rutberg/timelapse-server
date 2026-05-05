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

---

## Security Note

By default, the server is configured to only allow connections from the local network. Ensure `TIMELAPSE_ALLOWED_NETWORKS` in your server environment file correctly reflects your LAN setup.
