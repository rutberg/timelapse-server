# External Integrations

**Analysis Date:** 2026-05-08

## Hardware Interfaces

**Pi Camera (rpicam backend):**
- Tools: `rpicam-still`, `libcamera-still`, `raspistill` (tried in order)
- Interface: CLI subprocess from `agent/timelapse_agent.py` (`find_capture_command`, `build_capture_command`)
- Capabilities: JPEG capture with configurable width, height, quality; YUV420 light sampling (`sample_light_level`)
- Detection: `shutil.which()` for binary availability

**USB DSLR (gphoto2 backend):**
- Tool: `gphoto2` CLI (libgphoto2 PTP/MTP protocol over USB)
- Interface: CLI subprocess from `agent/timelapse_agent.py`
- Operations used:
  - `gphoto2 --auto-detect` — detect camera presence
  - `gphoto2 --capture-image-and-download --filename` — capture + download in one step
  - `gphoto2 --capture-image` — trigger-only (stores on SD card)
  - `gphoto2 --folder --get-file` — download from camera SD
  - `gphoto2 --folder --delete-file` — delete from camera SD
  - `gphoto2 --set-config <key>=<value>` — apply settings
  - `gphoto2 --get-config <key>` — read settings / choices
  - `gphoto2 --list-all-config` — property discovery
  - `gphoto2 --set-config autopoweroff=0` — disable auto power-off
- Vendor support: Canon, Nikon, Sony, Fuji, Olympus, Panasonic (via `server/app/dslr_discovery.py`)
- USB access: udev rule `SUBSYSTEMS=="usb", ATTRS{bDeviceClass}=="06", GROUP="plugdev", MODE="0664"` written by provision script
- gvfs conflict mitigation: `systemctl mask gvfs-gphoto2-volume-monitor.service`

## Internal Network Protocol (Server ↔ Agent)

**Transport:** HTTP/1.1 over LAN (no TLS by default)

**Server-side LAN restriction:** Middleware in `server/app/main.py` (`lan_only_middleware`) blocks any request from outside `TIMELAPSE_ALLOWED_NETWORKS`.

**Agent → Server endpoints (called from `agent/timelapse_agent.py`):**
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/cameras/{id}/config` | GET | Fetch remote camera config (polling, default 60s) |
| `/api/cameras/{id}/checkin` | POST | Heartbeat: agent version, status, telemetry |
| `/api/cameras/{id}/upload` | POST | Multipart JPEG upload with `X-Captured-At` header |
| `/api/cameras/{id}/update-manifest` | GET | Check for agent updates |
| `/api/releases/{filename}` | GET | Download agent release tarball |
| `/api/cameras/{id}/dslr/discovery/result` | POST | Post gphoto2 config tree after discovery run |

**Agent HTTP client:** Python stdlib `urllib.request.urlopen` (no third-party HTTP library on agent).

**Auth:** Network-level only (LAN IP allowlist). No token or session auth between server and agent.

## SSH (Server → Pi Provisioning)

**Tools used:** `ssh`, `scp`, `ssh-keygen` (system binaries, called via subprocess)
- Key type: Ed25519, generated per agent (`server/app/ssh_keys.py`)
- Key storage: `{DATA_DIR}/agents/{agent_id}/id_ed25519` (private), `id_ed25519.pub` (public)
- Post-provisioning: private key moved to `{DATA_DIR}/agents/{agent_id}/archive/id_ed25519` on first successful checkin (`server/app/agents.py` `KeyArchive`)
- Known hosts: per-agent file at `{DATA_DIR}/agents/{agent_id}/known_hosts`; `StrictHostKeyChecking=accept-new`
- Host resolution: mDNS (`{hostname}.local`) with IP fallback (`server/app/ssh_provision.py`)
- Provision flow: SSH → create staging dir → SCP payload files → pipe bash install script over SSH (`server/app/provision_script.py`)

## Video Rendering (ffmpeg)

**Tool:** `ffmpeg` (system binary, required on server)
- Interface: async subprocess via `asyncio.create_subprocess_exec` (`server/app/render_queue.py`)
- MP4 output: libx264 (software) or h264_vaapi (if `/dev/dri/renderD128` present)
- GIF output: two-pass (palettegen + paletteuse), 720px wide, lanczos scaling
- Progress: parsed from `ffmpeg -progress pipe:1` stdout for real-time ETA
- Thumbnail generation: synchronous subprocess in `server/app/main.py` (`ensure_frame_thumbnail`), 320×320 scaled JPEG

**VA-API hardware acceleration:**
- Auto-detected at startup: `Path("/dev/dri/renderD128").exists()`
- Device: `/dev/dri/renderD128`
- Codec: `h264_vaapi`

## Data Storage

**Databases:**
- None — no relational database or document store

**Primary state store:**
- `{DATA_DIR}/config.json` — all camera configs and statuses (JSON, atomic write via temp file + rename)
- `{DATA_DIR}/agents/{agent_id}/manifest.json` — agent provisioning state

**File Storage (local filesystem):**
- Images: `{DATA_DIR}/images/{camera_id}/{YYYY-MM-DD}/{YYYYMMDDTHHmmSS}.jpg`
- Thumbnails: `{DATA_DIR}/thumbnails/{camera_id}/{day}/{filename}`
- Videos: `{DATA_DIR}/videos/{camera_id}/{stem}.mp4` or `.gif`
- Agent releases: `{DATA_DIR}/releases/timelapse-agent-{version}.tar.gz` + `.sha256`
- Agent SSH keys: `{DATA_DIR}/agents/{agent_id}/id_ed25519{,.pub}`
- Production data root: `/srv/timelapse` (set via `TIMELAPSE_DATA_DIR`)

**Agent local storage (on Pi):**
- Pending captures: `/var/lib/timelapse-agent/pending/*.jpg` + sidecar `.json`
- Camera-pending queue (gphoto2): `/var/lib/timelapse-agent/pending_camera_files.json`
- Cached server config: `/var/lib/timelapse-agent/server-config.json`
- gphoto2 staging: `/tmp/timelapse-agent-stage/` (tmpfs)
- Agent install: `/opt/timelapse-agent/{version}/`, symlink at `/opt/timelapse-agent/current`
- Agent config: `/etc/timelapse-agent/config.json`

**Caching:**
- No external cache (Redis etc.)
- Agent caches server config locally to survive network outages

## Authentication & Identity

**Auth Provider:** None (network-level only)
- Server enforces LAN IP allowlist via ASGI middleware (`server/app/main.py` `lan_only_middleware`)
- Agent provisioning uses Ed25519 SSH keys (one keypair per agent)
- No user authentication for the web UI

## Agent Updates (OTA)

**Mechanism:** Pull-based, agent-initiated
- Agent polls `/api/cameras/{id}/update-manifest` on each config poll cycle
- Server serves release tarballs from `{DATA_DIR}/releases/`
- Agent verifies SHA-256 before install (`agent/timelapse_agent.py` `verify_sha256`)
- Install: extract to `/opt/timelapse-agent/{version}/`, atomic symlink swap to `current`
- After update: agent exits; systemd `Restart=always` restarts on new version

**Release build:** `scripts/build-release.sh` → `dist/timelapse-agent-{version}.tar.gz`

## Monitoring & Observability

**Error Tracking:** None (no Sentry, Rollbar, etc.)

**Logs:**
- Server: Python `logging` to stdout; systemd captures via journald
- Agent: Python `logging` to stdout with `%(asctime)s %(levelname)s %(message)s` format; level via `LOG_LEVEL` env var (default: INFO)

**Metrics:** None (no Prometheus, StatsD, etc.)

**Camera status telemetry (inbound to server via checkin):**
- `agent_version`, `hostname`, `source_ip`
- `last_capture_at`, `last_upload_at`, `last_error`
- `pending_count`, `pending_bytes`
- `in_schedule`, `local_hour`, `current_light` (0-255 Y luma)
- `signal_dbm` (Wi-Fi RSSI via `iw dev wlan0 link`)
- `active_backend` (`rpicam` or `gphoto2`)
- DSLR telemetry: battery, available shots, shutter counter, exposure mode, lens, model

## CI/CD & Deployment

**Hosting:** Self-hosted Linux server (LAN)

**CI Pipeline:** None detected

**Server deployment:**
- systemd service: `server/systemd/timelapse-server.service`
- Install script: `scripts/install-server.sh` (deploys to `/opt/timelapse/server/`)
- Update script: `scripts/update-server.sh`
- Dev server: `scripts/dev-server.sh`

**Agent deployment:**
- Provisioned via SSH from the server UI (`/api/agents/{id}/provision`)
- OTA updates via release bundle mechanism (see Agent Updates above)

## Webhooks & Callbacks

**Incoming:** None (no external webhooks)

**Outgoing:** None (no webhooks to external services)

## Solar Position Calculation

**Implementation:** Custom NOAA solar approximation in `agent/timelapse_agent.py` (`solar_window` function)
- Used for `schedule_mode = "daylight"`: computes local sunrise/sunset from latitude/longitude/UTC offset
- No external API — pure math (NOAA equation-of-time + declination formula)
- Fallback when no coordinates configured: 06:00–20:00

---

*Integration audit: 2026-05-08*
