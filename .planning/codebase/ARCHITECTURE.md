<!-- refreshed: 2026-05-08 -->
# Architecture

**Analysis Date:** 2026-05-08

## System Overview

```text
┌─────────────────────────────────────────────────────────────────────┐
│                    Browser (SPA)                                     │
│   /static/v2/app.js  +  views/*.js  +  components/*.js              │
│   Hash-based router, registerView pattern, vanilla ES modules        │
└────────────────────────────┬────────────────────────────────────────┘
                             │  HTTP  (LAN-only, CIDR guard middleware)
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                  FastAPI Server  (uvicorn)                           │
│   server/app/main.py          — all routes & Pydantic models        │
│   server/app/render_queue.py  — async ffmpeg job runner             │
│   server/app/agents.py        — agent provisioning store            │
│   server/app/dslr_discovery.py — property-map proposer              │
│   server/app/ssh_provision.py  — SSH/SCP to Pi                      │
│   server/app/provision_script.py — install-script builder           │
│   server/app/ssh_keys.py       — ed25519 keypair generation         │
└──────┬───────────────────────────────────────────────────┬──────────┘
       │  REST (HTTP multipart / JSON)                     │  Filesystem
       ▼                                                   ▼
┌─────────────────────────┐            ┌────────────────────────────────┐
│  Pi Agent (systemd)     │            │  DATA_DIR  (default: ./data    │
│  agent/timelapse_       │            │  or /srv/timelapse in prod)     │
│  agent.py               │            │                                 │
│                         │            │  data/config.json  (cameras)   │
│  • rpicam-still / gphoto│            │  data/images/<cam>/<day>/*.jpg │
│  • capture → pending/   │            │  data/thumbnails/<cam>/…       │
│  • upload → /upload     │            │  data/videos/<cam>/*.mp4|gif   │
│  • POST /checkin (60s)  │            │  data/agents/<id>/manifest.json│
│  • GET /config (60s)    │            │  data/agents/<id>/id_ed25519   │
│  • GET /update-manifest │            │  data/releases/*.tar.gz        │
│  • solar/scene/hours    │            └────────────────────────────────┘
│    scheduling           │
└─────────────────────────┘
```

## Component Responsibilities

| Component | Responsibility | File |
|-----------|----------------|------|
| FastAPI app | All HTTP routes, Pydantic models, store I/O | `server/app/main.py` |
| RenderRunner | Async ffmpeg job queue (MP4 / GIF) with ETA tracking | `server/app/render_queue.py` |
| AgentStore | Per-agent manifest persistence under `data/agents/` | `server/app/agents.py` |
| KeyArchive | Archives private SSH key after first agent checkin | `server/app/agents.py` |
| dslr_discovery | Turns `gphoto2 --list-all-config` tree into a `DslrPropertyMap` proposal | `server/app/dslr_discovery.py` |
| ssh_provision | SSH/SCP execution to install agent on Pi | `server/app/ssh_provision.py` |
| provision_script | Generates the bash install script sent to Pi | `server/app/provision_script.py` |
| ssh_keys | Generates ed25519 keypair via `ssh-keygen` | `server/app/ssh_keys.py` |
| Pi agent | Single-file capture daemon (rpicam-still or gphoto2) | `agent/timelapse_agent.py` |
| SPA shell | Hash router, shared fetchJson/utilities, view registry | `server/app/static/v2/app.js` |
| Dashboard view | Adaptive camera wall (solo/duo/trio/dense modes) | `server/app/static/v2/views/dashboard.js` |
| Camera view | Per-camera tabs: overview, frames, schedule, renders, settings | `server/app/static/v2/views/camera.js` |
| Library view | Global render history, new-render modal | `server/app/static/v2/views/library.js` |
| Schedule component | Three-mode schedule control (daylight/hours/scene) | `server/app/static/v2/components/schedule.js` |
| Sidebar component | Camera list nav with inline status badges | `server/app/static/v2/components/sidebar.js` |

## Pattern Overview

**Overall:** Client-server appliance with no database. The server is a single FastAPI process backed by a flat JSON file (`data/config.json`). Each Pi runs a standalone Python agent that polls the server for config and pushes frames via HTTP multipart.

**Key Characteristics:**
- No ORM or database — all persistence is plain JSON via atomic `tempfile + replace`
- Server models defined as Pydantic `BaseModel` classes directly in `main.py`; no separate models layer
- The agent is a single-file script with zero third-party imports (uses only stdlib) for easy deployment
- Video rendering is fully async via `asyncio.create_subprocess_exec`; all other endpoints are sync fastapi handlers run in uvicorn's thread pool
- LAN-only access enforced by middleware checking client IP against configurable CIDR list

## Layers

**HTTP API Layer:**
- Purpose: Exposes REST endpoints to the browser SPA and the Pi agents
- Location: `server/app/main.py`
- Contains: Route handlers, Pydantic request/response models, store load/save helpers, thumbnail generation (via ffmpeg subprocess), validation helpers
- Depends on: `agents.py`, `render_queue.py`, `dslr_discovery.py`, `ssh_provision.py`, `provision_script.py`, `ssh_keys.py`
- Used by: SPA browser, Pi agents

**Render Queue:**
- Purpose: Non-blocking ffmpeg execution with progress tracking and cancellation
- Location: `server/app/render_queue.py`
- Contains: `RenderRunner` (asyncio worker + reaper task), `JobState` dataclass, range preset resolver
- Depends on: nothing (pure asyncio + subprocess)
- Used by: `main.py` via module-level `_runner` singleton started in `@app.on_event("startup")`

**Agent Provisioning Subsystem:**
- Purpose: Manage the lifecycle of remote Pi agents (create → SSH provision → first checkin → archive key)
- Location: `server/app/agents.py`, `server/app/ssh_provision.py`, `server/app/provision_script.py`, `server/app/ssh_keys.py`
- Contains: `AgentStore` (per-agent manifest), `KeyArchive` (post-provision key archival), `run_provision` (SSH/SCP), `build_install_script` (bash generator)
- Depends on: filesystem, system `ssh-keygen` / `ssh` / `scp` binaries
- Used by: `main.py` agent CRUD routes

**DSLR Discovery Subsystem:**
- Purpose: Auto-detect gphoto2 property keys for a specific camera body and propose a `DslrPropertyMap`
- Location: `server/app/dslr_discovery.py`
- Contains: Priority tables for settings/telemetry/init keys across Canon/Nikon/Sony; `propose_property_map()` pure function; `parse_list_all_config()` parser
- Depends on: nothing (pure Python, no IO)
- Used by: `main.py` `POST /dslr/discovery/result` endpoint

**Pi Agent:**
- Purpose: Capture frames and upload to server; poll config; self-update
- Location: `agent/timelapse_agent.py`
- Contains: Capture backends (rpicam, gphoto2), schedule evaluation (solar/hours/scene), pending queue management, self-update via signed tarball, DSLR discovery runner
- Depends on: stdlib only; system binaries `rpicam-still`/`libcamera-still`/`raspistill`/`gphoto2`/`iw`
- Used by: systemd service on Pi (`agent/systemd/timelapse-agent.service`)

**SPA Frontend:**
- Purpose: Single-page dashboard for managing cameras, viewing frames, triggering renders
- Location: `server/app/static/v2/`
- Contains: `app.js` (router + shared utilities), view modules (`views/*.js`), component modules (`components/*.js`)
- Depends on: `/api/*` HTTP endpoints, native `fetch`, ES modules (no bundler)
- Used by: browser; served as static files by FastAPI

## Data Flow

### Agent Capture → Server Storage

1. Agent wakes at `next_capture` time, calls `capture_frame()` (`agent/timelapse_agent.py:1330`)
2. rpicam: runs `rpicam-still`/`libcamera-still` → writes `pending/<timestamp>.jpg` + `.json` sidecar
3. gphoto2: runs `gphoto2 --capture-image-and-download` → writes same structure; camera-side queue managed in `pending_camera_files.json`
4. `upload_pending()` iterates `pending/` and POSTs each to `POST /api/cameras/{id}/upload` with `X-Captured-At` header (`agent/timelapse_agent.py:1411`)
5. Server: `upload_image()` calls `parse_capture_time()` then `image_path()` to compute `data/images/{cam}/{day}/{timestamp}.jpg` (`server/app/main.py:1400`)
6. Server enqueues thumbnail generation as a `BackgroundTask` via `ensure_frame_thumbnail()` (ffmpeg)

### Agent Config Poll / Checkin

1. Every 60s: `fetch_remote_config()` GETs `GET /api/cameras/{id}/config` → overwrites `server-config.json` cache
2. Agent calls `post_checkin()` with `AgentState` fields: version, hostname, pending counts, DSLR telemetry, schedule status
3. Server `post_checkin()`: updates `status` on the camera record in `data/config.json`, sets `last_seen`; triggers `KeyArchive.archive_private_key()` on first successful checkin

### Video Render

1. Browser POSTs `POST /api/cameras/{id}/videos` with fps/format/date range
2. `generate_video()` in `main.py:1562` resolves images, writes a concat list file to `data/videos/{cam}/{stem}.txt`
3. Creates a `JobState` and calls `get_runner().enqueue_with_inputs()` → returns `{job_id, status: "queued", position}`
4. `RenderRunner._worker_loop()` pops the job, calls `_run_mp4()` or `_run_gif()`, streams ffmpeg `-progress pipe:1` output to update `percent` / `eta_seconds`
5. Browser polls `GET /api/renders/{job_id}` to show progress; `GET /api/cameras/{id}/videos/{filename}` to download

### Agent Provisioning

1. UI calls `POST /api/agents` → `AgentStore.create()` writes `manifest.json`, `generate_keypair()` writes `data/agents/{id}/id_ed25519`
2. UI calls `POST /api/agents/{id}/provision` → `resolve_target()` resolves Pi hostname/IP, `build_install_script()` produces bash, `run_provision()` SCPs `timelapse_agent.py` + service file, pipes script to remote `bash -s`
3. Agent starts, checks in → server triggers `KeyArchive.archive_private_key()`, moving private key to `data/agents/{id}/archive/id_ed25519`

### DSLR Discovery

1. UI calls `POST /api/cameras/{id}/dslr/discovery` → server mints a token, stores it as `config.dslr_pending_discovery`
2. Agent's next config poll finds the token, calls `run_dslr_discovery()` → runs `gphoto2 --list-all-config`, parses with `parse_list_all_config()`, POSTs to `POST /api/cameras/{id}/dslr/discovery/result`
3. Server calls `propose_property_map()` in `dslr_discovery.py`, stores proposal; UI fetches `GET /api/cameras/{id}/dslr/discovery/proposal`
4. User confirms → UI calls `PUT /api/cameras/{id}/dslr/property_map` to persist it into `config.dslr_property_map`

**State Management:**
- Server: all mutable state lives in `data/config.json` (cameras) and per-agent `manifest.json` files; `RenderRunner` holds in-memory job state only (not persisted across restarts)
- Agent: mutable state held in `AgentState` dataclass (`agent/timelapse_agent.py:56`); schedule config cached in `server-config.json` for offline operation

## Key Abstractions

**CameraRecord / CameraConfig / CameraStatus:**
- Purpose: Camera's configuration (user-controlled) plus live status (agent-pushed)
- Location: `server/app/main.py:194–357`
- Pattern: Pydantic models with field validators; stored flat as JSON dict in `data/config.json["cameras"][camera_id]`

**JobState:**
- Purpose: Tracks a single ffmpeg render job (queued → running → done/failed/cancelled)
- Location: `server/app/render_queue.py:17`
- Pattern: `@dataclass` with `to_dict()` for JSON serialization; held in `RenderRunner._jobs` dict

**AgentState:**
- Purpose: Agent's in-memory view of its own status (uploaded to server each heartbeat)
- Location: `agent/timelapse_agent.py:56`
- Pattern: `@dataclass` with optional fields; mutated in-place inside `run_agent()` loop

**DslrPropertyMap:**
- Purpose: Per-body schema describing which gphoto2 keys map to which UI controls
- Location: `server/app/main.py:160–167`; proposer in `server/app/dslr_discovery.py`
- Pattern: Pydantic model persisted inside the camera's config JSON; agent reads it from server config

## Entry Points

**Server:**
- Location: `server/app/main.py` — `app = FastAPI(...)` at line 39
- Triggers: `uvicorn app.main:app` (see `server/systemd/timelapse-server.service`)
- Responsibilities: All HTTP routing, startup (`RenderRunner.start()`), shutdown

**Agent:**
- Location: `agent/timelapse_agent.py:main()` at line 1641
- Triggers: `python3 /opt/timelapse-agent/current/timelapse_agent.py --config /etc/timelapse-agent/config.json`
- Responsibilities: Capture loop, upload loop, config poll loop, self-update check

## Architectural Constraints

- **Threading:** FastAPI runs sync endpoints in uvicorn's default thread pool. One mutex (`_feature_lock`) guards the feature-flag endpoint's read-modify-write. The `RenderRunner` runs on the asyncio event loop; all its methods must be called from async context.
- **Global state:** `_runner: Optional[RenderRunner]` at `server/app/main.py:43` is a module-level singleton; `ALLOWED_NETWORKS` is module-level constant computed from env var at import time.
- **Circular imports:** `render_queue` is imported inside functions in `main.py` (e.g. `from app.render_queue import resolve_range_preset`) to avoid a circular dependency with `main.py`'s module-level `from app.render_queue import RenderRunner`.
- **No restart persistence for renders:** `RenderRunner` in-memory state is not persisted; renders in progress at server restart are lost (no recovery).
- **Store contention:** `load_store()` / `save_store()` are called on every write request — no caching layer. Atomic writes use `tempfile.NamedTemporaryFile` + `Path.replace()`.

## Anti-Patterns

### All routes in one file
**What happens:** Every API route and every Pydantic model is defined in `server/app/main.py` (1700+ lines).
**Why it's wrong:** Merging all routes, models, and helpers makes the file hard to navigate and increases merge conflict risk.
**Do this instead:** As the API grows, extract domain groups (e.g. cameras, renders, agents) into separate router modules using `fastapi.APIRouter`, then `app.include_router(...)` in `main.py`.

### In-process sync SSH during HTTP request
**What happens:** `provision_agent()` (`main.py:1020`) calls `run_provision()` synchronously in a FastAPI sync handler, which may block for up to 600 seconds (SSH timeout).
**Why it's wrong:** This ties up one uvicorn worker thread for the full provision duration, preventing other requests from being served.
**Do this instead:** Move provisioning to a background task or a separate async subprocess with status polling similar to the `RenderRunner` pattern.

## Error Handling

**Strategy:** HTTP exceptions via `raise HTTPException(status_code=..., detail=...)` throughout `main.py`. Agent swallows transient upload/checkin errors with `logging.warning` and retries on the next loop iteration.

**Patterns:**
- Server returns 404 for missing cameras/agents/frames, 400 for validation failures, 502 for SSH provision errors
- Agent catches `(HTTPError, URLError, TimeoutError, json.JSONDecodeError)` on all outbound requests and logs warnings without crashing
- Render failures propagate as `RuntimeError` caught in `RenderRunner._worker_loop`, stored in `job.error`
- Config migrations handled by `migrate_camera_record()` at store load time

## Cross-Cutting Concerns

**Logging:** Standard `logging` module. Server uses FastAPI/uvicorn default; agent configures `logging.basicConfig` with `LOG_LEVEL` env var (default INFO).
**Validation:** Input validation via Pydantic field validators in `main.py`; camera IDs validated by `VALID_CAMERA_ID_RE` regex; agent config keys validated with regex in `provision_script.py`.
**Authentication:** None — access is restricted to configurable CIDR ranges via `lan_only_middleware` (`main.py:928`). No user accounts or API tokens.

---

*Architecture analysis: 2026-05-08*
