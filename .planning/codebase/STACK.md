# Technology Stack

**Analysis Date:** 2026-05-08

## Languages

**Primary:**
- Python 3.11 - Server (`server/`) and agent (`agent/timelapse_agent.py`)

**Secondary:**
- JavaScript (ES Modules, vanilla, no transpiler) - Browser frontend (`server/app/static/v2/`)
- Bash - Provisioning scripts (`scripts/`, `server/app/provision_script.py`-generated)

## Runtime

**Environment:**
- Python 3.11.2 (server venv: `server/.venv/pyvenv.cfg`, dev venv: `.venv-dev/pyvenv.cfg`)
- Browser: native ES Module support required (no bundler, no polyfills)

**Package Manager:**
- pip + venv
- Server production venv: `server/.venv/`
- Development/test venv: `.venv-dev/`
- No lockfile (only `server/requirements.txt` with `>=` bounds)

## Frameworks

**Core:**
- FastAPI 0.136.1 — REST API server (`server/app/main.py`)
- Pydantic 2.13.3 — Data validation and models (all request/response models in `server/app/main.py`)
- Starlette 1.0.0 — ASGI foundation (included transitively via FastAPI)

**Testing:**
- pytest 8.4.2 — Test runner (configured in `pyproject.toml`)
- pytest-asyncio (included via anyio 4.13.0) — Async test support
- httpx 0.28.1 + httpcore 1.0.9 — HTTP client used in tests (dev venv only)

**Build/Dev:**
- uvicorn 0.46.0 with uvloop 0.22.1 — ASGI server (production via systemd)
- watchfiles 1.1.1, websockets 16.0, httptools 0.7.1 — uvicorn standard extras

## Key Dependencies

**Critical:**
- `fastapi>=0.110,<1` — REST API (`server/requirements.txt`)
- `uvicorn[standard]>=0.27,<1` — ASGI server
- `python-multipart>=0.0.9` — Multipart file upload (`/api/cameras/{id}/upload`)
- `pydantic-core 2.46.3` — Runtime for Pydantic v2 validation

**Infrastructure (dev venv only):**
- `pytest-asyncio` — `asyncio_mode = "auto"` in `pyproject.toml`
- `python-dotenv 1.2.2` — Dev environment configuration
- `pyyaml 6.0.3` — Dev tooling

**Agent (no external deps):**
- The agent `agent/timelapse_agent.py` uses only Python stdlib (`urllib`, `subprocess`, `json`, `tarfile`, `hashlib`, `math`, etc.)

## Configuration

**Environment (server):**
- `TIMELAPSE_DATA_DIR` — Data root (default: `./data`; production: `/srv/timelapse`)
- `TIMELAPSE_ALLOWED_NETWORKS` — IP networks allowed LAN access (default: `127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,::1/128,fc00::/7,fe80::/10`)
- `TIMELAPSE_PUBLIC_URL` — Override for agent callback URL
- `TIMELAPSE_BIND_HOST`, `TIMELAPSE_PORT` — Bind address/port (systemd service)
- Optional: `EnvironmentFile=-/etc/timelapse-server/server.env`

**Agent:**
- Config file: `/etc/timelapse-agent/config.json` (JSON, required keys: `camera_id`, `server_url`)
- Optional: `config_poll_seconds` (default: 60), `work_dir` (default: `/var/lib/timelapse-agent`)
- `TIMELAPSE_WIFI_IFACE` — Wi-Fi interface for RSSI reading (default: `wlan0`)
- `LOG_LEVEL` — Log verbosity (default: `INFO`)

**Build:**
- `pyproject.toml` — Project metadata and pytest config (`testpaths`, `asyncio_mode`)
- `scripts/build-release.sh` — Packages agent into `dist/timelapse-agent-<version>.tar.gz` + `.sha256`

## Platform Requirements

**Development:**
- Python 3.11+
- pip + venv
- ffmpeg (for video render and thumbnail generation)
- ssh-keygen, ssh, scp (for agent provisioning)

**Production (server):**
- Linux (systemd service: `server/systemd/timelapse-server.service`)
- Deployed to `/opt/timelapse/server/` with venv at `/opt/timelapse/server/.venv/`
- Data at `/srv/timelapse/`
- Port: 8080 (configurable)
- ffmpeg — required for video rendering (MP4 via libx264 or h264_vaapi, GIF) and thumbnail generation
- Optional: `/dev/dri/renderD128` present enables VA-API hardware encoding (`server/app/render_queue.py`)

**Production (agent, on Raspberry Pi):**
- Python 3 (stdlib only, no pip install needed)
- Installed at `/opt/timelapse-agent/current/timelapse_agent.py`
- Config at `/etc/timelapse-agent/config.json`
- Work dir at `/var/lib/timelapse-agent/`
- rpicam-apps-lite (`rpicam-still`) or libcamera-still or raspistill — Pi camera capture
- gphoto2 — USB DSLR capture
- iw — Wi-Fi RSSI reading (optional)
- systemd (service: `agent/systemd/timelapse-agent.service`)

---

*Stack analysis: 2026-05-08*
