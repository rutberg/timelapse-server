# Hydroponic Timelapse

Local-network timelapse capture for a Raspberry Pi Zero W with a Raspberry Pi Camera Module.

The design is server-controlled:

1. The Pi agent polls the Debian server for its camera config.
2. The server returns the current capture interval, optional resolution limit, JPEG quality, and enabled state.
3. The Pi captures images locally with `rpicam-still` and uploads them back to the server.
4. The server stores images by camera/date and can generate MP4 videos with `ffmpeg`.

This keeps the Pi simple: it does not need a public IP, inbound SSH, Tailscale, direct remote control, or an auth token.

## Layout

- `server/` - FastAPI app for config, uploads, storage, and video generation.
- `agent/` - Raspberry Pi capture/upload agent.
- `agent/systemd/` - systemd unit for running the Pi agent.
- `server/systemd/` - systemd unit for running the server.

## Server Quick Start

On the Debian LXC at `192.168.68.52`:

```bash
sudo apt update
sudo apt install -y python3 python3-venv ffmpeg
cd /opt/timelapse/server
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export TIMELAPSE_DATA_DIR=/srv/timelapse
export TIMELAPSE_ALLOWED_NETWORKS="127.0.0.0/8,192.168.68.0/22"
uvicorn app.main:app --host 192.168.68.52 --port 8080
```

Health check:

```bash
curl http://192.168.68.52:8080/api/health
```

## Set Capture Interval From Server

Example: configure camera `tomatoes-zero-w` to capture every 10 minutes:

```bash
curl -X PUT http://192.168.68.52:8080/api/cameras/tomatoes-zero-w/config \
  -H 'Content-Type: application/json' \
  -d '{
    "enabled": true,
    "interval_seconds": 600,
    "image_width": null,
    "image_height": null,
    "jpeg_quality": 85
  }'
```

The Pi agent polls this config periodically and adjusts without a restart. `null` width/height means full sensor resolution.

## Pi Agent Quick Start

On Raspberry Pi OS Lite 32-bit, Raspberry Pi OS Lite includes `rpicam-apps-lite` according to Raspberry Pi camera docs. If missing, install it:

```bash
sudo apt update
sudo apt install -y rpicam-apps-lite python3
rpicam-hello --list-cameras
```

Copy `agent/timelapse_agent.py` to `/opt/timelapse-agent/timelapse_agent.py`, copy `agent/config.example.json` to `/etc/timelapse-agent/config.json`, then edit:

```json
{
  "camera_id": "tomatoes-zero-w",
  "server_url": "http://192.168.68.52:8080"
}
```

Install and start the service:

```bash
sudo cp agent/systemd/timelapse-agent.service /etc/systemd/system/timelapse-agent.service
sudo systemctl daemon-reload
sudo systemctl enable --now timelapse-agent
sudo journalctl -u timelapse-agent -f
```

## Generate A Video

```bash
curl -X POST http://192.168.68.52:8080/api/cameras/tomatoes-zero-w/videos \
  -H 'Content-Type: application/json' \
  -d '{"fps": 24}'
```

## LAN-Only Access

The server defaults to accepting only loopback and private LAN source IPs. For your LXC, the systemd unit binds directly to `192.168.68.52` and restricts requests to `127.0.0.0/8,192.168.68.0/22`.

If the LAN subnet changes, update:

```bash
TIMELAPSE_ALLOWED_NETWORKS="127.0.0.0/8,192.168.68.0/22"
```

## Image Build Path

Start with Raspberry Pi OS Lite plus the agent install steps. Once validated, bake the same files and service into a Pi-gen stage to produce a ready-to-burn SD-card image.
