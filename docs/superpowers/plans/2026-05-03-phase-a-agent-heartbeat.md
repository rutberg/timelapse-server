# Phase A: Agent Identity & Heartbeat — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Server tracks every camera's identity, online status, agent version, and last-seen time. Agent sends heartbeats so the server knows the Pi is alive even when it isn't capturing.

**Architecture:** Restructure the persisted camera record from a flat config dict into `{config, status}`. Add a `POST /api/cameras/{id}/checkin` endpoint. Agent posts to it on every config-poll tick (currently every 60s). Replace the silent ID-rewrite in `safe_identifier` with strict validation.

**Tech Stack:** Python 3.11+, FastAPI, pytest, httpx (via FastAPI's `TestClient`).

---

## Wave structure

```
Wave 1 (sequential):  A1 → A2
Wave 2 (parallel):    [A3 → A4]   (server branch — same file, sequential)
                      [A5]        (agent branch — independent file)
Wave 3 (sequential):  A6 → A7
```

Wave 2 dispatches one subagent per branch. The server branch runs A3 then A4 in sequence inside that subagent. The agent branch runs A5 alone in another subagent.

---

## File structure

**New files:**
- `tests/__init__.py` — empty marker.
- `tests/conftest.py` — shared pytest fixtures (`tmp_data_dir`, `client`).
- `tests/server/__init__.py`
- `tests/server/test_camera_record.py`
- `tests/server/test_checkin.py`
- `tests/server/test_camera_id_validation.py`
- `tests/server/test_camera_listing.py`
- `tests/agent/__init__.py`
- `tests/agent/test_heartbeat.py`
- `pyproject.toml` — pytest config + project metadata.
- `requirements-dev.txt` — pytest, httpx.

**Modified files:**
- `server/app/main.py` — `CameraRecord` model, migration on load, checkin endpoint, strict ID validation, enriched listing.
- `agent/timelapse_agent.py` — `post_checkin()` helper + invocation in poll loop.
- `agent/config.example.json` — add a comment line about heartbeats (no shape change).
- `README.md` — document the heartbeat behavior in Usage & API.

---

### Task A1: Test scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `requirements-dev.txt`
- Create: `tests/__init__.py` (empty)
- Create: `tests/conftest.py`
- Create: `tests/server/__init__.py` (empty)
- Create: `tests/agent/__init__.py` (empty)
- Create: `tests/server/test_smoke.py`

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["server", "agent"]
addopts = "-q"
```

- [ ] **Step 2: Create `requirements-dev.txt`**

```
-r server/requirements.txt
pytest>=8,<9
httpx>=0.27,<1
```

- [ ] **Step 3: Create the three empty package markers**

```bash
: > tests/__init__.py
: > tests/server/__init__.py
: > tests/agent/__init__.py
```

- [ ] **Step 4: Create `tests/conftest.py`**

```python
from __future__ import annotations

import importlib
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def tmp_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TIMELAPSE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv(
        "TIMELAPSE_ALLOWED_NETWORKS",
        "127.0.0.0/8,::1/128",
    )
    return tmp_path


@pytest.fixture
def client(tmp_data_dir: Path) -> Iterator[TestClient]:
    import app.main as server_main

    importlib.reload(server_main)
    with TestClient(server_main.app) as test_client:
        yield test_client
```

- [ ] **Step 5: Create `tests/server/test_smoke.py`**

```python
def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 6: Install dev deps and run the smoke test**

Run:
```bash
python3 -m venv .venv-dev
. .venv-dev/bin/activate
pip install -r requirements-dev.txt
pytest tests/server/test_smoke.py -v
```
Expected: 1 passed.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml requirements-dev.txt tests/
git commit -m "test: add pytest scaffolding with FastAPI TestClient"
```

---

### Task A2: Camera record schema + migration

**Files:**
- Modify: `server/app/main.py` — add `CameraStatus`, `CameraRecord`; migrate on load.
- Test: `tests/server/test_camera_record.py`

The existing `cameras[id]` dict holds config fields directly. We will move config under a `config` key and add a parallel `status` key. The loader must transparently migrate old-shape stores in place so an in-flight LXC keeps working after upgrade.

- [ ] **Step 1: Write the failing test for migration**

`tests/server/test_camera_record.py`:
```python
import json
from pathlib import Path


def write_legacy_store(data_dir: Path) -> None:
    legacy = {
        "cameras": {
            "tomatoes": {
                "enabled": True,
                "interval_seconds": 600,
                "image_width": 1920,
                "image_height": 1080,
                "jpeg_quality": 90,
                "config_version": 4,
            }
        }
    }
    (data_dir / "config.json").write_text(json.dumps(legacy), encoding="utf-8")


def test_legacy_store_is_migrated_on_read(tmp_data_dir, client):
    write_legacy_store(tmp_data_dir)

    response = client.get("/api/cameras/tomatoes/config")

    assert response.status_code == 200
    assert response.json()["interval_seconds"] == 600
    assert response.json()["image_width"] == 1920


def test_status_block_exists_after_migration(tmp_data_dir, client):
    write_legacy_store(tmp_data_dir)

    client.get("/api/cameras/tomatoes/config")
    raw = json.loads((tmp_data_dir / "config.json").read_text(encoding="utf-8"))

    record = raw["cameras"]["tomatoes"]
    assert "config" in record
    assert "status" in record
    assert record["status"]["last_seen"] is None
    assert record["status"]["agent_version"] is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/server/test_camera_record.py -v`
Expected: FAIL — current store stays flat, no `config`/`status` keys.

- [ ] **Step 3: Add the new models to `server/app/main.py`**

Insert after the existing `CameraConfig` class:

```python
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
```

- [ ] **Step 4: Add a migration helper**

Insert above `load_store`:

```python
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
```

- [ ] **Step 5: Apply migration inside `load_store`**

Replace the body of `load_store` with:

```python
def load_store() -> Dict[str, Any]:
    ensure_data_dir()
    if not STORE_PATH.exists():
        return {"cameras": {}}
    with STORE_PATH.open("r", encoding="utf-8") as store_file:
        store = json.load(store_file)
    cameras = store.setdefault("cameras", {})
    for camera_id, record in cameras.items():
        cameras[camera_id] = migrate_camera_record(record)
    return store
```

- [ ] **Step 6: Update `get_camera_config` and `set_camera_config`**

Replace both with:

```python
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
```

- [ ] **Step 7: Drop the unused `config_version` field from `CameraConfig`**

Remove the `config_version: int = 1` line. (We discussed dropping this in the review — nothing reads it.)

- [ ] **Step 8: Update `camera_summary` to read the nested config**

Replace `camera_summary`:

```python
def camera_summary(camera_id: str, record: Dict[str, Any]) -> Dict[str, Any]:
    images = list_camera_images(camera_id)
    latest = images[-1] if images else None
    return {
        "camera_id": camera_id,
        "config": record.get("config", {}),
        "status": record.get("status", {}),
        "image_count": len(images),
        "latest_image": str(latest.relative_to(DATA_DIR)) if latest else None,
    }
```

- [ ] **Step 9: Run tests**

Run: `pytest tests/server -v`
Expected: all pass (smoke + 2 new tests).

- [ ] **Step 10: Commit**

```bash
git add server/app/main.py tests/server/test_camera_record.py
git commit -m "feat(server): nested camera record with config+status and legacy migration"
```

---

### Task A3: Check-in endpoint (server branch, wave 2)

**Files:**
- Modify: `server/app/main.py`
- Test: `tests/server/test_checkin.py`

- [ ] **Step 1: Write the failing test**

`tests/server/test_checkin.py`:
```python
def test_checkin_records_status_fields(client):
    response = client.post(
        "/api/cameras/tomatoes/checkin",
        json={
            "agent_version": "0.3.1",
            "hostname": "timelapse-tomatoes",
            "last_capture_at": "2026-05-03T12:00:00Z",
            "last_upload_at": "2026-05-03T12:00:05Z",
            "last_error": None,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["acknowledged"] is True

    detail = client.get("/api/cameras").json()
    record = next(c for c in detail["cameras"] if c["camera_id"] == "tomatoes")
    assert record["status"]["agent_version"] == "0.3.1"
    assert record["status"]["hostname"] == "timelapse-tomatoes"
    assert record["status"]["last_seen"] is not None
    assert record["status"]["source_ip"] == "testclient"


def test_checkin_with_error_persists_error(client):
    client.post(
        "/api/cameras/tomatoes/checkin",
        json={"agent_version": "0.3.1", "last_error": "camera not detected"},
    )
    record = next(
        c for c in client.get("/api/cameras").json()["cameras"]
        if c["camera_id"] == "tomatoes"
    )
    assert record["status"]["last_error"] == "camera not detected"


def test_checkin_creates_camera_if_missing(client):
    response = client.post(
        "/api/cameras/new-camera/checkin",
        json={"agent_version": "0.3.1"},
    )
    assert response.status_code == 200
    cameras = client.get("/api/cameras").json()["cameras"]
    assert any(c["camera_id"] == "new-camera" for c in cameras)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/server/test_checkin.py -v`
Expected: FAIL — endpoint does not exist (404).

- [ ] **Step 3: Add the request model**

Insert after `CameraRecord`:

```python
class CheckinRequest(BaseModel):
    agent_version: Optional[str] = None
    hostname: Optional[str] = None
    last_capture_at: Optional[str] = None
    last_upload_at: Optional[str] = None
    last_error: Optional[str] = None
```

- [ ] **Step 4: Add the endpoint**

Insert near the other camera endpoints:

```python
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
    return {"acknowledged": True, "last_seen": now_iso}
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/server/test_checkin.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add server/app/main.py tests/server/test_checkin.py
git commit -m "feat(server): add /api/cameras/{id}/checkin heartbeat endpoint"
```

---

### Task A4: Strict camera_id validation (server branch, wave 2)

**Files:**
- Modify: `server/app/main.py`
- Test: `tests/server/test_camera_id_validation.py`

`safe_identifier` currently rewrites invalid characters to `-`, which silently maps mismatched IDs onto a different camera. Replace with a validator that rejects with HTTP 400.

- [ ] **Step 1: Write the failing tests**

`tests/server/test_camera_id_validation.py`:
```python
def test_invalid_id_is_rejected(client):
    response = client.get("/api/cameras/has space/config")
    assert response.status_code == 400
    assert "camera id" in response.json()["detail"].lower()


def test_valid_id_is_accepted(client):
    response = client.get("/api/cameras/timelapse-tomatoes_01.cam/config")
    assert response.status_code == 200


def test_empty_id_is_rejected(client):
    response = client.get("/api/cameras/-/config")
    assert response.status_code == 400


def test_id_with_slash_is_rejected_via_checkin(client):
    response = client.post(
        "/api/cameras/path%2Ftraversal/checkin",
        json={"agent_version": "0.0.1"},
    )
    assert response.status_code == 400
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/server/test_camera_id_validation.py -v`
Expected: failures (the rewriter currently accepts all of these).

- [ ] **Step 3: Replace `safe_identifier` with a validator**

Replace the function and the regex with:

```python
VALID_CAMERA_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}$")


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
```

(Drop `IDENTIFIER_RE`, it's no longer used.)

- [ ] **Step 4: Update video filename handling**

The video endpoint reuses `safe_identifier` to validate filenames. Filenames need to allow `.mp4`, which the regex already permits. Verify by reading the existing `read_video` function — it already strips and re-validates. No change required, but the request name in `generate_video` may now be rejected for legacy values. Update the error message there to be specific:

In `generate_video`, replace:
```python
requested_name = safe_identifier(request.name) if request.name else f"timelapse-{timestamp}"
```
with:
```python
if request.name:
    try:
        requested_name = safe_identifier(request.name)
    except HTTPException as error:
        raise HTTPException(status_code=400, detail=f"Invalid video name: {error.detail}") from error
else:
    requested_name = f"timelapse-{timestamp}"
```

- [ ] **Step 5: Run all server tests**

Run: `pytest tests/server -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add server/app/main.py tests/server/test_camera_id_validation.py
git commit -m "fix(server): reject invalid camera ids instead of silently rewriting"
```

---

### Task A5: Agent heartbeat (agent branch, wave 2 — runs in parallel with A3+A4)

**Files:**
- Modify: `agent/timelapse_agent.py`
- Test: `tests/agent/test_heartbeat.py`

Add an `AGENT_VERSION` constant, track last capture/upload/error timestamps in memory, and post them to `/checkin` on every config-poll tick.

- [ ] **Step 1: Write the failing test**

`tests/agent/test_heartbeat.py`:
```python
import json
from unittest.mock import MagicMock, patch

import timelapse_agent as agent


def test_post_checkin_sends_expected_payload():
    settings = {
        "camera_id": "tomatoes",
        "server_url": "http://server.local:8080",
    }
    state = agent.AgentState(
        last_capture_at="2026-05-03T12:00:00Z",
        last_upload_at="2026-05-03T12:00:05Z",
        last_error=None,
    )

    captured: dict = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["headers"] = dict(request.headers)
        response = MagicMock()
        response.read.return_value = b'{"acknowledged": true}'
        response.__enter__ = lambda self: self
        response.__exit__ = lambda self, *args: None
        return response

    with patch.object(agent, "urlopen", side_effect=fake_urlopen):
        agent.post_checkin(settings, state)

    assert captured["url"] == "http://server.local:8080/api/cameras/tomatoes/checkin"
    assert captured["body"]["agent_version"] == agent.AGENT_VERSION
    assert captured["body"]["last_capture_at"] == "2026-05-03T12:00:00Z"
    assert captured["body"]["last_upload_at"] == "2026-05-03T12:00:05Z"
    assert captured["body"]["last_error"] is None
    assert captured["body"]["hostname"]


def test_post_checkin_swallows_network_errors():
    settings = {"camera_id": "tomatoes", "server_url": "http://nope:8080"}
    state = agent.AgentState()

    def fake_urlopen(*args, **kwargs):
        raise agent.URLError("nope")

    with patch.object(agent, "urlopen", side_effect=fake_urlopen):
        agent.post_checkin(settings, state)
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/agent/test_heartbeat.py -v`
Expected: FAIL — `AGENT_VERSION`, `AgentState`, and `post_checkin` don't exist.

- [ ] **Step 3: Add `AGENT_VERSION`, `AgentState`, and `post_checkin`**

In `agent/timelapse_agent.py`, near the top after imports:

```python
AGENT_VERSION = "0.3.0"
```

After `DEFAULT_REMOTE_CONFIG`:

```python
from dataclasses import dataclass, field


@dataclass
class AgentState:
    last_capture_at: Optional[str] = None
    last_upload_at: Optional[str] = None
    last_error: Optional[str] = None
```

After `request_json`:

```python
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
```

- [ ] **Step 4: Wire `post_checkin` into the run loop**

Replace `run_agent` body's locals/loop section. The loop becomes:

```python
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
```

- [ ] **Step 5: Update `upload_pending` to set `last_upload_at`**

Replace its signature and body:

```python
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
```

- [ ] **Step 6: Run agent tests**

Run: `pytest tests/agent -v`
Expected: 2 passed.

- [ ] **Step 7: Commit**

```bash
git add agent/timelapse_agent.py tests/agent/test_heartbeat.py
git commit -m "feat(agent): post heartbeat on each config poll with version + state"
```

---

### Task A6: Online status derivation in listing (wave 3)

**Files:**
- Modify: `server/app/main.py`
- Test: `tests/server/test_camera_listing.py`

The PRD requires the server to expose "is online" — derive it from `last_seen` against a staleness threshold (3× the configured poll interval, or 5 minutes if unknown).

- [ ] **Step 1: Write the failing test**

`tests/server/test_camera_listing.py`:
```python
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


def test_camera_marked_offline_when_last_seen_old(tmp_data_dir, client):
    client.post(
        "/api/cameras/tomatoes/checkin",
        json={"agent_version": "0.3.0"},
    )

    raw = json.loads((tmp_data_dir / "config.json").read_text(encoding="utf-8"))
    old = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat().replace("+00:00", "Z")
    raw["cameras"]["tomatoes"]["status"]["last_seen"] = old
    (tmp_data_dir / "config.json").write_text(json.dumps(raw), encoding="utf-8")

    record = next(
        c for c in client.get("/api/cameras").json()["cameras"]
        if c["camera_id"] == "tomatoes"
    )
    assert record["status"]["is_online"] is False


def test_camera_marked_online_when_last_seen_recent(client):
    client.post("/api/cameras/tomatoes/checkin", json={"agent_version": "0.3.0"})
    record = next(
        c for c in client.get("/api/cameras").json()["cameras"]
        if c["camera_id"] == "tomatoes"
    )
    assert record["status"]["is_online"] is True


def test_camera_offline_when_never_seen(client):
    client.put(
        "/api/cameras/tomatoes/config",
        json={
            "enabled": True,
            "interval_seconds": 600,
            "image_width": None,
            "image_height": None,
            "jpeg_quality": 85,
        },
    )
    record = next(
        c for c in client.get("/api/cameras").json()["cameras"]
        if c["camera_id"] == "tomatoes"
    )
    assert record["status"]["is_online"] is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/server/test_camera_listing.py -v`
Expected: FAIL — `is_online` is not a field.

- [ ] **Step 3: Add the helper**

Insert above `camera_summary`:

```python
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
```

- [ ] **Step 4: Update `camera_summary`**

```python
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
```

- [ ] **Step 5: Run all server tests**

Run: `pytest tests/server -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add server/app/main.py tests/server/test_camera_listing.py
git commit -m "feat(server): derive is_online from last_seen in camera listing"
```

---

### Task A7: Documentation update

**Files:**
- Modify: `README.md`
- Modify: `agent/config.example.json` (no shape change — just verify it's still correct)

- [ ] **Step 1: Verify `agent/config.example.json` is unchanged**

```bash
cat agent/config.example.json
```
Expected: still has `camera_id`, `server_url`, `config_poll_seconds`, `work_dir`. No edits needed.

- [ ] **Step 2: Add Heartbeat section to `README.md`**

Insert under `## Usage & API`, before `### Setting Camera Configuration`:

```markdown
### Camera Status

Each agent posts a heartbeat to `POST /api/cameras/<id>/checkin` on every config-poll tick. The server tracks `last_seen`, `agent_version`, `hostname`, and the most recent capture/upload/error. List all cameras with their current status:

```bash
curl http://<SERVER_IP>:8080/api/cameras
```

A camera is considered online if its last heartbeat was within five minutes (or three poll intervals, whichever is larger).
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document agent heartbeat and is_online derivation"
```

---

## Self-review checklist (run after Task A7)

- [ ] All tests pass: `pytest tests/ -v`
- [ ] No `config_version` references remain in server code (we dropped the field).
- [ ] Existing `/api/cameras/{id}/config` GET/PUT contract unchanged for the agent (still returns flat `CameraConfig`).
- [ ] Migration is idempotent: running it twice on a migrated record returns the same record.
- [ ] `safe_identifier` is referenced everywhere a camera id enters the API.
