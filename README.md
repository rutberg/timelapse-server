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
- `image/` - Tooling for creating headless, "flash and forget" SD card images.
- `scripts/` - Automated installation and update scripts for the server.

---

## Server Setup

The server is designed to run on a Debian-based system (e.g., a Debian LXC, Ubuntu VM, or a dedicated server).

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

## Agent Setup

The agent runs on a Raspberry Pi with a camera module.

### Manual Installation (Existing Pi)

1.  **Install dependencies:**
    ```bash
    sudo apt update
    sudo apt install -y rpicam-apps-lite python3
    ```

2.  **Configure:**
    Copy `agent/config.example.json` to `/etc/timelapse-agent/config.json` and edit it with your server details:
    ```json
    {
      "camera_id": "my-pi-camera",
      "server_url": "http://<SERVER_IP>:8080"
    }
    ```

3.  **Install Service:**
    ```bash
    sudo cp agent/systemd/timelapse-agent.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable --now timelapse-agent
    ```

### Headless SD Image (Recommended)

For a "flash and forget" setup, use the provided patching script to bake Wi-Fi and server credentials directly into an official Raspberry Pi OS image.

1.  **Prepare config:**
    ```bash
    cp image/headless.example.env image/headless.env
    # Edit image/headless.env with your SSID, Password, and Server URL
    ```

2.  **Patch image:**
    ```bash
    sudo image/patch-raspios-image.sh \
      --image raspios-bookworm-armhf-lite.img \
      --config image/headless.env \
      --output my-timelapse-pi.img
    ```

---

## Usage & API

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

---

## Security Note

By default, the server is configured to only allow connections from the local network. Ensure `TIMELAPSE_ALLOWED_NETWORKS` in your server environment file correctly reflects your LAN setup.
