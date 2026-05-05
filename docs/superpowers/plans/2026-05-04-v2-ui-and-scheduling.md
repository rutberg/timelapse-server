# v2 UI + Scheduling Modes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Pico/Alpine UI with the dark-themed v2 handoff design, and extend the scheduler with three modes: **Daylight** (sunrise/sunset auto), **Hours** (start/end window — replaces the 24-cell grid), and **Scene light** (luminance-gated capture from a pre-capture YUV sample).

**Architecture:** Backend gains new optional fields on `CameraConfig` (`schedule_mode`, `schedule_days`, `light_threshold`, `display_name`, `latitude`, `longitude`) and `CameraStatus` (`current_light`, `signal_dbm`); a new `stats` block on `GET /api/cameras`; a `DELETE /api/cameras/:id` endpoint; and GIF support via ffmpeg's two-pass palette pipeline on the videos endpoint. Agent gains `schedule_days` honouring, NOAA sunrise/sunset for daylight mode, and a YUV luma sample for scene mode. Frontend is a 1:1 drop-in of the design bundle's `v2/` tree.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, pytest, vanilla ES modules (no build), ffmpeg.

**Spec:** [`docs/superpowers/specs/2026-05-04-v2-ui-and-scheduling-design.md`](../specs/2026-05-04-v2-ui-and-scheduling-design.md)

---

## Wave structure

```
Wave 1 (sequential):  T1 → T2 → T3 → T4 → T5    (server/main.py — same file, sequential)
Wave 2 (parallel):    [T6 → T7 → T8 → T9]       (agent branch — agent/timelapse_agent.py)
                      [T10 → T11 → T12]         (frontend branch — server/app/static/)
Wave 3 (sequential):  T13 → T14 → T15
```

Wave 1 must finish before either Wave 2 branch starts (both depend on the new server schema). Wave 2's two branches are fully independent — dispatch as parallel subagents. Wave 3 happens after both Wave 2 branches converge.

---

## File structure

**New files:**
- `server/app/static/v2/index.html`, `app.js`, `styles.css`, `components/sidebar.js`, `components/schedule.js`, `views/dashboard.js`, `views/camera.js`, `views/create-agent.js`, `views/library.js`, `views/settings.js` — copied verbatim from the handoff bundle, with two edits noted in T10–T12.
- `tests/server/test_schedule_modes.py` — schema validation for the three modes.
- `tests/server/test_stats.py` — stats block on `/api/cameras`.
- `tests/server/test_camera_delete.py` — DELETE endpoint.
- `tests/server/test_video_format.py` — GIF format on POST videos.
- `tests/agent/test_schedule_days.py`
- `tests/agent/test_solar.py` — sunrise/sunset calc.
- `tests/agent/test_scene_light.py` — YUV luma sampling + capture decision.

**Modified files:**
- `server/app/main.py` — schema extensions, stats, DELETE, GIF format, route to `v2/index.html`.
- `agent/timelapse_agent.py` — schedule_days, daylight mode, scene mode, current_light + signal_dbm in heartbeat.
- `agent/VERSION` — bump to `0.8.0`.
- `README.md` — document new scheduling modes briefly.

**Deleted files:**
- `server/app/static/index.html`
- `server/app/static/app.js`
- `server/app/static/styles.css`
- `server/app/static/views/dashboard.js`
- `server/app/static/views/camera.js`
- `server/app/static/views/create-agent.js`
- `server/app/static/vendor/alpine.min.js`
- `server/app/static/vendor/pico.min.css`

---

## WAVE 1 — Server schema and API

### Task T1: Extend `CameraConfig` with new fields

**Files:**
- Modify: `server/app/main.py` (`CameraConfig` class around line 50)
- Test: `tests/server/test_schedule_modes.py` (new)

- [ ] **Step 1: Write failing tests**

```python
# tests/server/test_schedule_modes.py
import pytest
from app.main import CameraConfig


class TestScheduleModes:
    def test_default_mode_is_none(self):
        cfg = CameraConfig()
        assert cfg.schedule_mode is None
        assert cfg.schedule_days is None
        assert cfg.light_threshold is None
        assert cfg.display_name is None
        assert cfg.latitude is None
        assert cfg.longitude is None

    def test_hours_mode_requires_capture_hours(self):
        with pytest.raises(ValueError, match="capture_hours"):
            CameraConfig(schedule_mode="hours", capture_hours=None)

    def test_hours_mode_with_hours_ok(self):
        cfg = CameraConfig(schedule_mode="hours", capture_hours=[6, 7, 8])
        assert cfg.capture_hours == [6, 7, 8]

    def test_daylight_mode_clears_capture_hours(self):
        cfg = CameraConfig(schedule_mode="daylight", capture_hours=[1, 2, 3])
        assert cfg.capture_hours is None  # forced to null

    def test_scene_mode_requires_threshold(self):
        with pytest.raises(ValueError, match="light_threshold"):
            CameraConfig(schedule_mode="scene", light_threshold=None)

    def test_scene_mode_clears_capture_hours(self):
        cfg = CameraConfig(schedule_mode="scene", light_threshold=60, capture_hours=[1, 2])
        assert cfg.capture_hours is None
        assert cfg.light_threshold == 60

    def test_scene_mode_threshold_range(self):
        with pytest.raises(ValueError):
            CameraConfig(schedule_mode="scene", light_threshold=300)
        with pytest.raises(ValueError):
            CameraConfig(schedule_mode="scene", light_threshold=-1)

    def test_invalid_mode_rejected(self):
        with pytest.raises(ValueError):
            CameraConfig(schedule_mode="never")

    def test_schedule_days_iso_weekday(self):
        cfg = CameraConfig(schedule_days=[1, 3, 5])
        assert cfg.schedule_days == [1, 3, 5]
        with pytest.raises(ValueError):
            CameraConfig(schedule_days=[0])  # Sunday-as-0 not allowed
        with pytest.raises(ValueError):
            CameraConfig(schedule_days=[8])
        with pytest.raises(ValueError):
            CameraConfig(schedule_days=[1, 1])  # duplicates rejected

    def test_latitude_longitude_range(self):
        cfg = CameraConfig(latitude=59.3, longitude=18.0)
        assert cfg.latitude == 59.3
        with pytest.raises(ValueError):
            CameraConfig(latitude=91.0)
        with pytest.raises(ValueError):
            CameraConfig(longitude=181.0)

    def test_display_name_optional(self):
        cfg = CameraConfig(display_name="Tomato camera")
        assert cfg.display_name == "Tomato camera"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/server/test_schedule_modes.py -v`
Expected: FAIL — `schedule_mode`, `schedule_days`, `light_threshold`, `display_name`, `latitude`, `longitude` are not fields of `CameraConfig`.

- [ ] **Step 3: Extend `CameraConfig` in `server/app/main.py`**

Replace the existing `CameraConfig` class (around line 50) with this version:

```python
class CameraConfig(BaseModel):
    enabled: bool = True
    interval_seconds: int = Field(900, ge=30, le=86_400)
    image_width: Optional[int] = Field(None, ge=320, le=10_000)
    image_height: Optional[int] = Field(None, ge=240, le=10_000)
    jpeg_quality: int = Field(85, ge=1, le=100)
    desired_agent_version: Optional[str] = Field(None, pattern=r"^[A-Za-z0-9._-]+$")
    capture_hours: Optional[List[int]] = Field(
        default=None,
        description=(
            "Hours of day (0-23, agent local time) when capture is allowed. "
            "None = always. Empty list rejected — use enabled=false to pause."
        ),
    )
    schedule_mode: Optional[str] = Field(
        default=None,
        description="One of 'daylight', 'hours', 'scene'. None = legacy/unset.",
    )
    schedule_days: Optional[List[int]] = Field(
        default=None,
        description="ISO weekdays (1=Mon..7=Sun) on which capture is allowed. None = every day.",
    )
    light_threshold: Optional[int] = Field(
        default=None,
        ge=0,
        le=255,
        description="Mean Y luma 0-255. Required when schedule_mode='scene'.",
    )
    display_name: Optional[str] = Field(
        default=None,
        max_length=120,
        description="Human-friendly camera label. Falls back to camera_id.",
    )
    latitude: Optional[float] = Field(default=None, ge=-90.0, le=90.0)
    longitude: Optional[float] = Field(default=None, ge=-180.0, le=180.0)

    @field_validator("capture_hours")
    @classmethod
    def _validate_capture_hours(cls, value):
        if value is None:
            return value
        if not value:
            raise ValueError(
                "capture_hours must be null or contain at least one hour; "
                "use enabled=false to pause"
            )
        seen = set()
        for hour in value:
            if not isinstance(hour, int) or isinstance(hour, bool):
                raise ValueError(f"capture_hours entries must be ints 0-23, got {hour!r}")
            if hour < 0 or hour > 23:
                raise ValueError(f"capture_hours entries must be 0-23, got {hour}")
            if hour in seen:
                raise ValueError(f"capture_hours has duplicate {hour}")
            seen.add(hour)
        return sorted(seen)

    @field_validator("schedule_mode")
    @classmethod
    def _validate_schedule_mode(cls, value):
        if value is None:
            return value
        if value not in ("daylight", "hours", "scene"):
            raise ValueError(
                f"schedule_mode must be 'daylight', 'hours', or 'scene'; got {value!r}"
            )
        return value

    @field_validator("schedule_days")
    @classmethod
    def _validate_schedule_days(cls, value):
        if value is None:
            return value
        seen = set()
        for day in value:
            if not isinstance(day, int) or isinstance(day, bool):
                raise ValueError(f"schedule_days entries must be ISO weekdays 1-7, got {day!r}")
            if day < 1 or day > 7:
                raise ValueError(f"schedule_days entries must be 1 (Mon) - 7 (Sun), got {day}")
            if day in seen:
                raise ValueError(f"schedule_days has duplicate {day}")
            seen.add(day)
        return sorted(seen)

    @model_validator(mode="after")
    def _enforce_mode_invariants(self):
        if self.schedule_mode == "hours":
            if not self.capture_hours:
                raise ValueError("schedule_mode='hours' requires non-empty capture_hours")
        elif self.schedule_mode == "daylight":
            self.capture_hours = None
        elif self.schedule_mode == "scene":
            if self.light_threshold is None:
                raise ValueError("schedule_mode='scene' requires light_threshold")
            self.capture_hours = None
        return self
```

Add `model_validator` to the existing pydantic import line:

```python
from pydantic import BaseModel, Field, field_validator, model_validator
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/server/test_schedule_modes.py -v`
Expected: PASS — all 11 tests.

- [ ] **Step 5: Verify existing tests still pass**

Run: `pytest tests/server/ -v`
Expected: All existing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add server/app/main.py tests/server/test_schedule_modes.py
git commit -m "feat(server): extend CameraConfig with schedule_mode, days, light, location"
```

---

### Task T2: Extend `CameraStatus` and `CheckinRequest`

**Files:**
- Modify: `server/app/main.py` (`CameraStatus`, `CheckinRequest` around lines 87–115, `post_checkin` around line 591)
- Test: `tests/server/test_checkin.py` (extend existing)

- [ ] **Step 1: Write failing test**

Append to `tests/server/test_checkin.py` (create if it doesn't exist already — it does, just add):

```python
def test_checkin_accepts_current_light(client, tmp_data_dir):
    response = client.post(
        "/api/cameras/cam-light/checkin",
        json={"agent_version": "0.8.0", "current_light": 142},
    )
    assert response.status_code == 200

    listing = client.get("/api/cameras").json()
    cam = next(c for c in listing["cameras"] if c["camera_id"] == "cam-light")
    assert cam["status"]["current_light"] == 142


def test_checkin_accepts_signal_dbm(client, tmp_data_dir):
    response = client.post(
        "/api/cameras/cam-rssi/checkin",
        json={"agent_version": "0.8.0", "signal_dbm": -62},
    )
    assert response.status_code == 200

    listing = client.get("/api/cameras").json()
    cam = next(c for c in listing["cameras"] if c["camera_id"] == "cam-rssi")
    assert cam["status"]["signal_dbm"] == -62


def test_checkin_current_light_range(client, tmp_data_dir):
    bad = client.post(
        "/api/cameras/cam-bad/checkin",
        json={"agent_version": "0.8.0", "current_light": 300},
    )
    assert bad.status_code == 422
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/server/test_checkin.py::test_checkin_accepts_current_light -v`
Expected: FAIL — `current_light` is not in `CheckinRequest` or `CameraStatus`.

- [ ] **Step 3: Extend `CameraStatus` and `CheckinRequest`**

In `server/app/main.py`, append two fields to `CameraStatus`:

```python
class CameraStatus(BaseModel):
    hostname: Optional[str] = None
    source_ip: Optional[str] = None
    last_seen: Optional[str] = None
    last_capture_at: Optional[str] = None
    last_upload_at: Optional[str] = None
    last_error: Optional[str] = None
    agent_version: Optional[str] = None
    pending_count: int = 0
    pending_bytes: int = 0
    in_schedule: Optional[bool] = None
    local_hour: Optional[int] = None
    current_light: Optional[int] = Field(default=None, ge=0, le=255)
    signal_dbm: Optional[int] = Field(default=None, ge=-120, le=0)
```

Append two fields to `CheckinRequest`:

```python
class CheckinRequest(BaseModel):
    agent_version: Optional[str] = None
    hostname: Optional[str] = None
    last_capture_at: Optional[str] = None
    last_upload_at: Optional[str] = None
    last_error: Optional[str] = None
    pending_count: Optional[int] = Field(default=None, ge=0)
    pending_bytes: Optional[int] = Field(default=None, ge=0)
    in_schedule: Optional[bool] = None
    local_hour: Optional[int] = Field(default=None, ge=0, le=23)
    current_light: Optional[int] = Field(default=None, ge=0, le=255)
    signal_dbm: Optional[int] = Field(default=None, ge=-120, le=0)
```

In `post_checkin` (the function around line 591), append after the `local_hour` block:

```python
    if payload.current_light is not None:
        status["current_light"] = payload.current_light
    if payload.signal_dbm is not None:
        status["signal_dbm"] = payload.signal_dbm
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/server/test_checkin.py -v`
Expected: PASS — both new tests plus the rejection test.

- [ ] **Step 5: Commit**

```bash
git add server/app/main.py tests/server/test_checkin.py
git commit -m "feat(server): accept current_light and signal_dbm in heartbeat"
```

---

### Task T3: Add `stats` block to `GET /api/cameras`

**Files:**
- Modify: `server/app/main.py` (the `list_cameras` handler — search for `@app.get("/api/cameras")`)
- Test: `tests/server/test_stats.py` (new)

- [ ] **Step 1: Locate the listing handler**

Run: `grep -n '@app.get("/api/cameras")' server/app/main.py`
Note the function name and line number.

- [ ] **Step 2: Write failing test**

```python
# tests/server/test_stats.py
import os
from pathlib import Path


def test_cameras_listing_includes_stats(client, tmp_data_dir):
    # Seed a camera so the listing isn't empty.
    client.post("/api/cameras/cam-stats/checkin", json={"agent_version": "0.8.0"})

    response = client.get("/api/cameras")
    assert response.status_code == 200
    body = response.json()

    assert "stats" in body
    stats = body["stats"]
    assert "storage_bytes" in stats
    assert "storage_capacity_bytes" in stats
    assert isinstance(stats["storage_bytes"], int)
    assert isinstance(stats["storage_capacity_bytes"], int)
    assert stats["storage_capacity_bytes"] >= stats["storage_bytes"]


def test_storage_bytes_counts_uploads(client, tmp_data_dir):
    # A small file written into the images tree should be reflected.
    images_dir = Path(tmp_data_dir) / "images" / "cam-bytes" / "2026-05-04"
    images_dir.mkdir(parents=True, exist_ok=True)
    (images_dir / "1234.jpg").write_bytes(b"x" * 1000)

    body = client.get("/api/cameras").json()
    assert body["stats"]["storage_bytes"] >= 1000
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/server/test_stats.py -v`
Expected: FAIL — `stats` key not in response.

- [ ] **Step 4: Implement `compute_stats` and wire into the listing**

Add this helper near the other top-level helpers in `server/app/main.py` (e.g. above `load_store`):

```python
def directory_size_bytes(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for entry in path.rglob("*"):
        try:
            if entry.is_file():
                total += entry.stat().st_size
        except OSError:
            continue
    return total


def compute_stats() -> Dict[str, int]:
    """Storage usage and capacity for the data directory's filesystem."""
    used = directory_size_bytes(DATA_DIR / "images") + directory_size_bytes(DATA_DIR / "videos")
    try:
        usage = shutil.disk_usage(str(DATA_DIR))
        capacity = usage.total
    except OSError:
        capacity = used  # degenerate fallback so the UI shows 100%
    return {"storage_bytes": int(used), "storage_capacity_bytes": int(capacity)}
```

In the `list_cameras` handler, change the return statement from `{"cameras": [...]}` to include the stats block:

```python
    return {"cameras": cameras, "stats": compute_stats()}
```

(Adjust to match the local variable name used for the assembled cameras list.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/server/test_stats.py tests/server/test_camera_listing.py -v`
Expected: PASS — both new tests, plus existing camera-listing tests.

- [ ] **Step 6: Commit**

```bash
git add server/app/main.py tests/server/test_stats.py
git commit -m "feat(server): expose storage stats on GET /api/cameras"
```

---

### Task T4: `DELETE /api/cameras/:id`

**Files:**
- Modify: `server/app/main.py` (add new endpoint near other camera endpoints)
- Test: `tests/server/test_camera_delete.py` (new)

- [ ] **Step 1: Write failing test**

```python
# tests/server/test_camera_delete.py
from pathlib import Path


def test_delete_removes_camera_record(client, tmp_data_dir):
    client.post("/api/cameras/cam-doomed/checkin", json={"agent_version": "0.8.0"})

    response = client.delete("/api/cameras/cam-doomed")
    assert response.status_code == 204

    listing = client.get("/api/cameras").json()
    ids = [c["camera_id"] for c in listing["cameras"]]
    assert "cam-doomed" not in ids


def test_delete_removes_image_directory(client, tmp_data_dir):
    img_dir = Path(tmp_data_dir) / "images" / "cam-imgs" / "2026-05-04"
    img_dir.mkdir(parents=True, exist_ok=True)
    (img_dir / "1.jpg").write_bytes(b"junk")

    response = client.delete("/api/cameras/cam-imgs")
    assert response.status_code == 204
    assert not (Path(tmp_data_dir) / "images" / "cam-imgs").exists()


def test_delete_unknown_camera_returns_204(client, tmp_data_dir):
    """Idempotent: deleting a never-existed camera is a no-op success."""
    response = client.delete("/api/cameras/cam-ghost")
    assert response.status_code == 204


def test_delete_validates_camera_id(client, tmp_data_dir):
    response = client.delete("/api/cameras/..bad..")
    assert response.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/server/test_camera_delete.py -v`
Expected: FAIL — DELETE endpoint not defined.

- [ ] **Step 3: Add the endpoint**

In `server/app/main.py`, near the other camera endpoints (e.g. just after `update_config`), add:

```python
@app.delete("/api/cameras/{camera_id}", status_code=204)
def delete_camera(camera_id: str) -> None:
    camera_id = safe_identifier(camera_id)
    store = load_store()
    cameras = store.setdefault("cameras", {})
    cameras.pop(camera_id, None)
    save_store(store)

    for sub in ("images", "videos"):
        path = DATA_DIR / sub / camera_id
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/server/test_camera_delete.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server/app/main.py tests/server/test_camera_delete.py
git commit -m "feat(server): add DELETE /api/cameras/:id"
```

---

### Task T5: Video render — accept `format=gif`

**Files:**
- Modify: `server/app/main.py` (`VideoRequest` and `generate_video` around line 156, 714)
- Test: `tests/server/test_video_format.py` (new)

- [ ] **Step 1: Write failing test**

```python
# tests/server/test_video_format.py
import shutil
from pathlib import Path
import pytest


@pytest.fixture(autouse=True)
def _seed_two_jpegs(tmp_data_dir):
    """Drop two minimal JPEG files into the images tree so ffmpeg has frames."""
    cam_dir = Path(tmp_data_dir) / "images" / "cam-vids" / "2026-05-04"
    cam_dir.mkdir(parents=True, exist_ok=True)
    # 1x1 white JPEG (simplest valid file ffmpeg accepts)
    minimal_jpeg = bytes.fromhex(
        "ffd8ffe000104a46494600010100000100010000ffdb0043"
        "00080606070605080707070909080a0c140d0c0b0b0c1912"
        "130f141d1a1f1e1d1a1c1c20242e2720222c231c1c282737"
        "292c30313434341f27393d38323c2e333432ffc000110800"
        "010001031100021101031101ffc4001f0000010501010101"
        "01010000000000000000010203040506070809000a0bffc4"
        "00b5100002010303020403050504040000017d0102030004"
        "11051221314106135161072271143281914250a16223756e"
        "4172d162f0247248323a242422a2b2c2d2e2f3334353637"
        "38393a434445464748494a535455565758595a636465666768"
        "696a737475767778797a838485868788898a92939495969798"
        "999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6"
        "c7c8c9cad2d3d4d5d6d7d8d9dae1e2e3e4e5e6e7e8e9eaf1f2"
        "f3f4f5f6f7f8f9faffc4001f01000301010101010101010101"
        "0000000000000102030405060708090a0bffc400b511000201"
        "020404030407050404000102770001020311040521310612"
        "415107617113223281081442911a1b1c1234454262415"
        "172d1a262433404343535354565756656556167778494a5354"
        "565758595a636465666768696a737475767778797a8283848"
        "5868788898a92939495969798999aa2a3a4a5a6a7a8a9aab"
        "2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8"
        "d9dae2e3e4e5e6e7e8e9eaf2f3f4f5f6f7f8f9faffda000c0"
        "3010002110311003f00f7e8a28affd9"
    )
    (cam_dir / "143000.jpg").write_bytes(minimal_jpeg)
    (cam_dir / "143005.jpg").write_bytes(minimal_jpeg)


def test_default_format_is_mp4(client):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    response = client.post(
        "/api/cameras/cam-vids/videos",
        json={"start_date": "2026-05-04", "end_date": "2026-05-04", "fps": 12},
    )
    assert response.status_code == 200
    assert response.json()["path"].endswith(".mp4")


def test_explicit_mp4_format(client):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    response = client.post(
        "/api/cameras/cam-vids/videos",
        json={"start_date": "2026-05-04", "end_date": "2026-05-04", "fps": 12, "format": "mp4"},
    )
    assert response.status_code == 200
    assert response.json()["path"].endswith(".mp4")


def test_gif_format(client):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    response = client.post(
        "/api/cameras/cam-vids/videos",
        json={"start_date": "2026-05-04", "end_date": "2026-05-04", "fps": 12, "format": "gif"},
    )
    assert response.status_code == 200
    assert response.json()["path"].endswith(".gif")


def test_invalid_format_rejected(client):
    response = client.post(
        "/api/cameras/cam-vids/videos",
        json={"start_date": "2026-05-04", "end_date": "2026-05-04", "fps": 12, "format": "webm"},
    )
    assert response.status_code == 422
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/server/test_video_format.py -v`
Expected: FAIL — `format` field not on `VideoRequest`.

- [ ] **Step 3: Extend `VideoRequest` and `generate_video`**

Replace the existing `VideoRequest` (around line 156) with:

```python
class VideoRequest(BaseModel):
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    fps: int = Field(24, ge=1, le=60)
    name: Optional[str] = None
    format: str = Field("mp4", pattern=r"^(mp4|gif)$")
```

Replace the body of `generate_video` (the function around line 714, keep the signature) with:

```python
@app.post("/api/cameras/{camera_id}/videos")
def generate_video(
    camera_id: str,
    request: VideoRequest,
) -> Dict[str, Any]:
    camera_id = safe_identifier(camera_id)
    images = selected_images(camera_id, request)
    if not images:
        raise HTTPException(status_code=404, detail="No images found for selection")

    if not shutil.which("ffmpeg"):
        raise HTTPException(status_code=500, detail="ffmpeg is not installed")

    video_dir = DATA_DIR / "videos" / camera_id
    video_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if request.name:
        try:
            requested_name = safe_identifier(request.name)
        except HTTPException as error:
            raise HTTPException(status_code=400, detail=f"Invalid video name: {error.detail}") from error
    else:
        requested_name = f"timelapse-{timestamp}"

    output_path = video_dir / f"{requested_name}.{request.format}"
    list_path = video_dir / f"{requested_name}.txt"

    with list_path.open("w", encoding="utf-8") as list_file:
        for path in images:
            list_file.write(f"file '{ffmpeg_escape(path)}'\n")

    try:
        if request.format == "mp4":
            run_ffmpeg_mp4(list_path, output_path, request.fps)
        else:
            run_ffmpeg_gif(list_path, output_path, request.fps, video_dir, requested_name)
    finally:
        list_path.unlink(missing_ok=True)

    return {
        "generated": True,
        "camera_id": camera_id,
        "image_count": len(images),
        "path": str(output_path.relative_to(DATA_DIR)),
    }


def run_ffmpeg_mp4(list_path: Path, output_path: Path, fps: int) -> None:
    command = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(list_path),
        "-vf", f"fps={fps},format=yuv420p",
        "-c:v", "libx264", "-movflags", "+faststart",
        str(output_path),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as error:
        raise HTTPException(status_code=500, detail=error.stderr.strip()) from error


def run_ffmpeg_gif(list_path: Path, output_path: Path, fps: int, work_dir: Path, base_name: str) -> None:
    palette_path = work_dir / f"{base_name}-palette.png"
    palette_command = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(list_path),
        "-vf", f"fps={fps},scale=720:-1:flags=lanczos,palettegen",
        str(palette_path),
    ]
    encode_command = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(list_path),
        "-i", str(palette_path),
        "-filter_complex", f"fps={fps},scale=720:-1:flags=lanczos[x];[x][1:v]paletteuse",
        str(output_path),
    ]
    try:
        subprocess.run(palette_command, check=True, capture_output=True, text=True)
        subprocess.run(encode_command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as error:
        raise HTTPException(status_code=500, detail=error.stderr.strip()) from error
    finally:
        palette_path.unlink(missing_ok=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/server/test_video_format.py -v`
Expected: PASS (or `skip` if ffmpeg isn't on the runner).

- [ ] **Step 5: Run the full server test suite**

Run: `pytest tests/server/ -v`
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add server/app/main.py tests/server/test_video_format.py
git commit -m "feat(server): GIF render via ffmpeg palette pipeline"
```

---

## WAVE 2A — Agent (parallel branch)

### Task T6: Honour `schedule_days` in scheduling

**Files:**
- Modify: `agent/timelapse_agent.py` (`hour_in_schedule` around line 100)
- Test: `tests/agent/test_schedule_days.py` (new)

- [ ] **Step 1: Write failing tests**

```python
# tests/agent/test_schedule_days.py
from datetime import datetime
from timelapse_agent import is_in_schedule


# Reference dates with known ISO weekdays:
# 2026-05-04 = Mon (1), 2026-05-08 = Fri (5), 2026-05-09 = Sat (6)
MON_NOON = datetime(2026, 5, 4, 12, 0)
FRI_NOON = datetime(2026, 5, 8, 12, 0)
SAT_NOON = datetime(2026, 5, 9, 12, 0)


class TestScheduleDays:
    def test_no_days_means_all_days(self):
        assert is_in_schedule(MON_NOON, capture_hours=None, schedule_days=None)
        assert is_in_schedule(SAT_NOON, capture_hours=None, schedule_days=None)

    def test_weekdays_only(self):
        weekdays = [1, 2, 3, 4, 5]
        assert is_in_schedule(MON_NOON, capture_hours=None, schedule_days=weekdays)
        assert is_in_schedule(FRI_NOON, capture_hours=None, schedule_days=weekdays)
        assert not is_in_schedule(SAT_NOON, capture_hours=None, schedule_days=weekdays)

    def test_empty_days_means_paused(self):
        assert not is_in_schedule(MON_NOON, capture_hours=None, schedule_days=[])

    def test_days_combine_with_hours(self):
        # On a permitted day inside the hour window: yes
        assert is_in_schedule(MON_NOON, capture_hours=[12], schedule_days=[1, 2])
        # On a permitted day OUTSIDE the hour window: no
        assert not is_in_schedule(MON_NOON, capture_hours=[6, 7], schedule_days=[1, 2])
        # On a forbidden day inside the hour window: no
        assert not is_in_schedule(SAT_NOON, capture_hours=[12], schedule_days=[1, 2])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/agent/test_schedule_days.py -v`
Expected: FAIL — `is_in_schedule` doesn't exist (only `hour_in_schedule` exists).

- [ ] **Step 3: Add `is_in_schedule` and refactor `hour_in_schedule`**

In `agent/timelapse_agent.py`, immediately below the existing `hour_in_schedule` function (around line 100), add:

```python
def is_in_schedule(
    now: datetime,
    capture_hours: Optional[list],
    schedule_days: Optional[list] = None,
) -> bool:
    """Combined gate: hour-of-day AND ISO-weekday must both allow capture.

    capture_hours: None = no hour restriction; list of 0-23 ints otherwise.
    schedule_days: None = every day; list of 1-7 ISO weekdays otherwise.
                   Empty list (length zero, not None) means "paused — no day enabled".
    """
    if schedule_days is not None:
        if now.isoweekday() not in set(schedule_days):
            return False
    return hour_in_schedule(now, capture_hours)
```

Update the call site in `run_agent` (search for `hour_in_schedule(local_now, capture_hours)` — there are two: one at startup and one in the main loop). Replace each with:

```python
in_schedule = is_in_schedule(local_now, capture_hours, remote_config.get("schedule_days"))
```

And the startup line similarly becomes:

```python
state.in_schedule = is_in_schedule(startup_now, remote_config.get("capture_hours"), remote_config.get("schedule_days"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/agent/test_schedule_days.py tests/agent/ -v`
Expected: PASS — new tests plus existing.

- [ ] **Step 5: Commit**

```bash
git add agent/timelapse_agent.py tests/agent/test_schedule_days.py
git commit -m "feat(agent): honour schedule_days in capture gating"
```

---

### Task T7: Daylight mode — sunrise/sunset

**Files:**
- Modify: `agent/timelapse_agent.py` (add `solar_window`, integrate into `is_in_schedule` flow)
- Test: `tests/agent/test_solar.py` (new)

- [ ] **Step 1: Write failing tests**

```python
# tests/agent/test_solar.py
from datetime import date
from timelapse_agent import solar_window, daylight_capture_hours


class TestSolarWindow:
    """Reference values from NOAA for known city/dates (rounded to nearest hour)."""

    def test_stockholm_summer_solstice(self):
        # Stockholm 2026-06-21 — sunrise ~03:31, sunset ~22:08 local; UTC offset +2.
        # We pass UTC offset 0 and expect the bare UTC sunrise/sunset hours.
        sunrise, sunset = solar_window(date(2026, 6, 21), 59.33, 18.07, utc_offset_hours=2.0)
        assert 3 <= sunrise <= 4
        assert 21 <= sunset <= 22

    def test_stockholm_winter_solstice(self):
        # 2026-12-21 — sunrise ~08:43, sunset ~14:48 local; UTC offset +1.
        sunrise, sunset = solar_window(date(2026, 12, 21), 59.33, 18.07, utc_offset_hours=1.0)
        assert 8 <= sunrise <= 9
        assert 14 <= sunset <= 15

    def test_equator_equinox(self):
        # Quito (~0,-78), 2026-03-20 — sunrise/sunset roughly 06:00/18:00 local (UTC-5).
        sunrise, sunset = solar_window(date(2026, 3, 20), 0.0, -78.5, utc_offset_hours=-5.0)
        assert 5 <= sunrise <= 7
        assert 17 <= sunset <= 19

    def test_polar_night_returns_empty(self):
        # 80°N in winter — sun never rises.
        result = solar_window(date(2026, 12, 21), 80.0, 0.0, utc_offset_hours=0.0)
        assert result is None

    def test_polar_day_returns_full(self):
        # 80°N in summer — sun never sets.
        result = solar_window(date(2026, 6, 21), 80.0, 0.0, utc_offset_hours=0.0)
        assert result == (0, 24)


class TestDaylightCaptureHours:
    def test_with_location(self):
        # Stockholm-ish in May — a wide window, certainly including noon and excluding 02:00.
        hours = daylight_capture_hours(
            today=date(2026, 5, 4), latitude=59.33, longitude=18.07, utc_offset_hours=2.0,
        )
        assert 12 in hours
        assert 2 not in hours

    def test_without_location_falls_back(self):
        # No lat/lon → fixed 06:00–20:00 fallback.
        hours = daylight_capture_hours(today=date(2026, 5, 4), latitude=None, longitude=None)
        assert hours == list(range(6, 20))

    def test_polar_night_returns_empty_list(self):
        hours = daylight_capture_hours(
            today=date(2026, 12, 21), latitude=80.0, longitude=0.0, utc_offset_hours=0.0,
        )
        assert hours == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/agent/test_solar.py -v`
Expected: FAIL — `solar_window` and `daylight_capture_hours` don't exist.

- [ ] **Step 3: Implement solar calculations in `agent/timelapse_agent.py`**

Add this block above `hour_in_schedule`:

Add `import math` and `from datetime import date` to the existing imports at the top of the file (alongside the existing `from datetime import datetime`).

```python
def solar_window(
    today: date,
    latitude: float,
    longitude: float,
    utc_offset_hours: float = 0.0,
) -> Optional[Tuple[int, int]]:
    """Return (sunrise_hour, sunset_hour_exclusive) in local time, or None.

    Uses the NOAA solar position approximation. Resolution is hour-rounded —
    enough for capture-window scheduling. Returns:
        (0, 24) when the sun never sets (polar day),
        None    when the sun never rises (polar night).
    """
    n = today.timetuple().tm_yday
    gamma = 2 * math.pi / 365 * (n - 1)

    # Equation of time (minutes)
    eqtime = 229.18 * (
        0.000075
        + 0.001868 * math.cos(gamma)
        - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2 * gamma)
        - 0.040849 * math.sin(2 * gamma)
    )

    # Solar declination (radians)
    decl = (
        0.006918
        - 0.399912 * math.cos(gamma)
        + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2 * gamma)
        + 0.000907 * math.sin(2 * gamma)
        - 0.002697 * math.cos(3 * gamma)
        + 0.00148 * math.sin(3 * gamma)
    )

    lat_rad = math.radians(latitude)
    # Hour angle at sunrise/sunset (zenith = 90.833° to include refraction).
    cos_ha = (math.cos(math.radians(90.833)) - math.sin(lat_rad) * math.sin(decl)) / (
        math.cos(lat_rad) * math.cos(decl)
    )
    if cos_ha < -1.0:
        return (0, 24)         # polar day
    if cos_ha > 1.0:
        return None            # polar night
    ha = math.degrees(math.acos(cos_ha))

    # Solar noon (UTC minutes)
    solar_noon_utc_min = 720 - 4 * longitude - eqtime
    sunrise_utc_min = solar_noon_utc_min - 4 * ha
    sunset_utc_min = solar_noon_utc_min + 4 * ha

    sunrise_local_h = (sunrise_utc_min / 60.0) + utc_offset_hours
    sunset_local_h = (sunset_utc_min / 60.0) + utc_offset_hours

    sunrise_h = max(0, min(23, int(math.floor(sunrise_local_h))))
    sunset_h = max(1, min(24, int(math.ceil(sunset_local_h))))
    return (sunrise_h, sunset_h)


def daylight_capture_hours(
    today: date,
    latitude: Optional[float],
    longitude: Optional[float],
    utc_offset_hours: float = 0.0,
) -> List[int]:
    """Hours of day to capture in 'daylight' mode. Falls back to 06:00–20:00."""
    if latitude is None or longitude is None:
        return list(range(6, 20))
    window = solar_window(today, latitude, longitude, utc_offset_hours)
    if window is None:
        return []
    sunrise_h, sunset_h = window
    return list(range(sunrise_h, sunset_h))
```

- [ ] **Step 4: Wire daylight mode into `is_in_schedule`'s call site**

In `run_agent`, just before the existing `is_in_schedule(...)` call, derive effective hours:

```python
        schedule_mode = remote_config.get("schedule_mode")
        if schedule_mode == "daylight":
            offset = local_now.utcoffset()
            offset_h = offset.total_seconds() / 3600 if offset else 0.0
            effective_hours = daylight_capture_hours(
                today=local_now.date(),
                latitude=remote_config.get("latitude"),
                longitude=remote_config.get("longitude"),
                utc_offset_hours=offset_h,
            )
        else:
            effective_hours = remote_config.get("capture_hours")

        in_schedule = is_in_schedule(local_now, effective_hours, remote_config.get("schedule_days"))
```

(Replace the prior `capture_hours = remote_config.get("capture_hours")` and `in_schedule = is_in_schedule(...)` lines.)

Apply the same change to the startup-time call.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/agent/test_solar.py tests/agent/ -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add agent/timelapse_agent.py tests/agent/test_solar.py
git commit -m "feat(agent): daylight mode via NOAA sunrise/sunset"
```

---

### Task T8: Scene-light mode — YUV luma sample + capture decision

**Files:**
- Modify: `agent/timelapse_agent.py` (add `sample_light_level`, integrate into capture loop, report `current_light` in heartbeat)
- Test: `tests/agent/test_scene_light.py` (new)

- [ ] **Step 1: Write failing tests**

```python
# tests/agent/test_scene_light.py
from unittest.mock import patch
from timelapse_agent import (
    mean_y_from_yuv,
    should_capture_for_scene,
)


class TestMeanYFromYuv:
    def test_uniform_dark(self):
        # 64*48 = 3072 bytes, all zero
        raw = bytes([0]) * (64 * 48)
        # Trailing UV planes don't affect Y mean
        raw += bytes([128]) * (64 * 48 // 2)
        assert mean_y_from_yuv(raw, width=64, height=48) == 0

    def test_uniform_bright(self):
        raw = bytes([255]) * (64 * 48) + bytes([128]) * (64 * 48 // 2)
        assert mean_y_from_yuv(raw, width=64, height=48) == 255

    def test_mid_grey(self):
        raw = bytes([128]) * (64 * 48) + bytes([128]) * (64 * 48 // 2)
        assert mean_y_from_yuv(raw, width=64, height=48) == 128

    def test_short_buffer_returns_none(self):
        assert mean_y_from_yuv(b"abc", width=64, height=48) is None


class TestSceneCaptureDecision:
    def test_above_threshold_captures(self):
        assert should_capture_for_scene(current_light=120, threshold=60) is True

    def test_below_threshold_skips(self):
        assert should_capture_for_scene(current_light=20, threshold=60) is False

    def test_equal_to_threshold_captures(self):
        assert should_capture_for_scene(current_light=60, threshold=60) is True

    def test_no_threshold_means_capture(self):
        assert should_capture_for_scene(current_light=20, threshold=None) is True

    def test_no_reading_means_capture(self):
        # Conservative: if we couldn't sample, don't gate the user out
        assert should_capture_for_scene(current_light=None, threshold=60) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/agent/test_scene_light.py -v`
Expected: FAIL — functions not defined.

- [ ] **Step 3: Implement luma helpers**

Add to `agent/timelapse_agent.py`, near the other capture helpers:

```python
def mean_y_from_yuv(raw: bytes, width: int, height: int) -> Optional[int]:
    """Mean Y luminance (0-255) from a raw YUV420 buffer's Y plane.

    YUV420 stores: width*height bytes of Y, then width*height/4 of U, then
    width*height/4 of V. We only need the Y plane.
    """
    y_plane_size = width * height
    if len(raw) < y_plane_size:
        return None
    plane = raw[:y_plane_size]
    return sum(plane) // len(plane)


def sample_light_level(command: str, width: int = 64, height: int = 48) -> Optional[int]:
    """Capture a tiny YUV thumbnail and return the mean Y luminance (0-255).

    Returns None if no capture tool is available or if the tool fails.
    """
    if command.endswith("raspistill"):
        cmd = [command, "-n", "-t", "200", "-w", str(width), "-h", str(height),
               "-e", "yuv", "-o", "-"]
    else:
        cmd = [command, "--nopreview", "--timeout", "200",
               "--width", str(width), "--height", str(height),
               "--encoding", "yuv420", "--output", "-"]
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, timeout=5)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as error:
        logging.warning("Light sample failed: %s", error)
        return None
    return mean_y_from_yuv(result.stdout, width, height)


def should_capture_for_scene(current_light: Optional[int], threshold: Optional[int]) -> bool:
    """Capture decision for scene-light mode.

    Conservative defaults: missing threshold or missing reading both return True
    (don't gate captures out due to misconfiguration or transient sample failures).
    """
    if threshold is None or current_light is None:
        return True
    return current_light >= threshold
```

- [ ] **Step 4: Wire into capture loop and heartbeat**

In `run_agent`'s main loop (after `in_schedule = is_in_schedule(...)` from T7):

```python
        # Sample scene light when in scene mode (and only when capture is otherwise allowed).
        light_reading: Optional[int] = None
        if schedule_mode == "scene" and enabled and in_schedule and now >= next_capture:
            tool = find_capture_command()
            if tool:
                light_reading = sample_light_level(tool)
                state.current_light = light_reading
            threshold = remote_config.get("light_threshold")
            if not should_capture_for_scene(light_reading, threshold):
                logging.info("Scene-light gate: Y=%s < threshold=%s, skipping", light_reading, threshold)
                next_capture = now + remote_config.get("interval_seconds", 900)  # reschedule for next tick
                # fall through; the existing capture branch's condition `now >= next_capture`
                # will be false, so no capture.
```

Add `current_light: Optional[int] = None` to `AgentState`:

```python
@dataclass
class AgentState:
    last_capture_at: Optional[str] = None
    last_upload_at: Optional[str] = None
    last_error: Optional[str] = None
    pending_count: int = 0
    pending_bytes: int = 0
    in_schedule: bool = True
    local_hour: int = 0
    current_light: Optional[int] = None
```

In `post_checkin` (the agent function around line 320), append `current_light` to the payload:

```python
    payload = {
        "agent_version": settings.get("agent_version"),
        "hostname": socket.gethostname(),
        "last_capture_at": state.last_capture_at,
        "last_upload_at": state.last_upload_at,
        "last_error": state.last_error,
        "pending_count": state.pending_count,
        "pending_bytes": state.pending_bytes,
        "in_schedule": state.in_schedule,
        "local_hour": state.local_hour,
        "current_light": state.current_light,
    }
```

(Adjust to match the exact key set the existing function builds; just add `current_light` to it.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/agent/test_scene_light.py tests/agent/ -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add agent/timelapse_agent.py tests/agent/test_scene_light.py
git commit -m "feat(agent): scene-light capture gating via YUV luma sample"
```

---

### Task T9: Report `signal_dbm` in heartbeat

**Files:**
- Modify: `agent/timelapse_agent.py` (add `read_wifi_rssi`, append to heartbeat)
- Test: existing `tests/agent/test_heartbeat.py`

- [ ] **Step 1: Add the helper**

In `agent/timelapse_agent.py`, near the other `subprocess`-using helpers:

```python
def read_wifi_rssi() -> Optional[int]:
    """Read Wi-Fi RSSI in dBm from `iw dev`. Linux-only; returns None on any failure.

    Parses the line `signal: -57 dBm` from `iw dev wlan0 link` output.
    """
    if not shutil.which("iw"):
        return None
    interface = os.environ.get("TIMELAPSE_WIFI_IFACE", "wlan0")
    try:
        result = subprocess.run(
            ["iw", "dev", interface, "link"],
            check=False, capture_output=True, text=True, timeout=2,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("signal:"):
            try:
                return int(line.split()[1])
            except (IndexError, ValueError):
                return None
    return None
```

- [ ] **Step 2: Append to heartbeat payload**

In the `post_checkin` agent function, append `signal_dbm`:

```python
        "current_light": state.current_light,
        "signal_dbm": read_wifi_rssi(),
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/agent/ -v`
Expected: All pass (the existing heartbeat tests don't assert on signal_dbm; they just need not to choke on the extra field).

- [ ] **Step 4: Commit**

```bash
git add agent/timelapse_agent.py
git commit -m "feat(agent): report Wi-Fi signal_dbm in heartbeat"
```

---

## WAVE 2B — Frontend (parallel branch)

### Task T10: Drop in v2 static tree

**Files:**
- Create: 10 files under `server/app/static/v2/` (copied from the design bundle)
- Modify: `server/app/main.py` (root route serves `v2/index.html`)

The v2 source files live in the design bundle at `/tmp/design3-extracted/timelapse/project/server/app/static/v2/`. Copy them verbatim with two edits described below.

- [ ] **Step 1: Copy the v2 tree**

```bash
mkdir -p /root/timelapse/server/app/static/v2/components /root/timelapse/server/app/static/v2/views
cp /tmp/design3-extracted/timelapse/project/server/app/static/v2/index.html /root/timelapse/server/app/static/v2/
cp /tmp/design3-extracted/timelapse/project/server/app/static/v2/styles.css /root/timelapse/server/app/static/v2/
cp /tmp/design3-extracted/timelapse/project/server/app/static/v2/app.js /root/timelapse/server/app/static/v2/
cp /tmp/design3-extracted/timelapse/project/server/app/static/v2/components/sidebar.js /root/timelapse/server/app/static/v2/components/
cp /tmp/design3-extracted/timelapse/project/server/app/static/v2/components/schedule.js /root/timelapse/server/app/static/v2/components/
cp /tmp/design3-extracted/timelapse/project/server/app/static/v2/views/dashboard.js /root/timelapse/server/app/static/v2/views/
cp /tmp/design3-extracted/timelapse/project/server/app/static/v2/views/camera.js /root/timelapse/server/app/static/v2/views/
cp /tmp/design3-extracted/timelapse/project/server/app/static/v2/views/create-agent.js /root/timelapse/server/app/static/v2/views/
cp /tmp/design3-extracted/timelapse/project/server/app/static/v2/views/library.js /root/timelapse/server/app/static/v2/views/
cp /tmp/design3-extracted/timelapse/project/server/app/static/v2/views/settings.js /root/timelapse/server/app/static/v2/views/
```

- [ ] **Step 2: Remove the Google Fonts `<link>` from v2 `index.html`**

Edit `/root/timelapse/server/app/static/v2/index.html`:

Replace:
```html
  <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet" />
  <link rel="stylesheet" href="/static/v2/styles.css" />
```

With:
```html
  <link rel="stylesheet" href="/static/v2/styles.css" />
```

(Just delete the Google Fonts line.)

- [ ] **Step 3: Update `main.py` root and SPA-fallback routes**

In `server/app/main.py`, the root route around line 439:

```python
@app.get("/", include_in_schema=False)
def root() -> FileResponse:
    return FileResponse(STATIC_DIR / "v2" / "index.html", media_type="text/html")
```

And the SPA fallback at line 805:

```python
    return FileResponse(STATIC_DIR / "v2" / "index.html", media_type="text/html")
```

(Change both `STATIC_DIR / "index.html"` references to `STATIC_DIR / "v2" / "index.html"`.)

- [ ] **Step 4: Smoke test**

```bash
cd /root/timelapse
TIMELAPSE_DATA_DIR=./dev-data .venv-dev/bin/python -m uvicorn server.app.main:app --port 8765 &
sleep 2
curl -s http://localhost:8765/ | head -20
curl -s http://localhost:8765/static/v2/styles.css | head -5
kill %1
```

Expected: index.html returns the new dark-themed shell; styles.css returns CSS starting with `/* Timelapse v2 — design tokens lifted from the prototype */`.

- [ ] **Step 5: Commit**

```bash
git add server/app/static/v2/ server/app/main.py
git commit -m "feat(ui): drop in v2 dark-themed static tree, system fonts"
```

---

### Task T11: Render modal — MP4 + GIF, drop WebM

**Files:**
- Modify: `server/app/static/v2/views/library.js`

- [ ] **Step 1: Open `library.js` and locate the format segment**

The line is:
```js
${["mp4","gif","webm"].map(f=>`<button data-fmt="${f}" class="${f===format?"active":""}">${f.toUpperCase()}</button>`).join("")}
```

- [ ] **Step 2: Replace `webm` reference**

Change to:
```js
${["mp4","gif"].map(f=>`<button data-fmt="${f}" class="${f===format?"active":""}">${f.toUpperCase()}</button>`).join("")}
```

- [ ] **Step 3: Smoke test**

Run a server, open the library view's New Render modal in a browser, click MP4 then GIF — both should toggle active. WebM should be absent.

- [ ] **Step 4: Commit**

```bash
git add server/app/static/v2/views/library.js
git commit -m "feat(ui): render modal — MP4 + GIF only (drop WebM)"
```

---

### Task T12: Remove old static tree

**Files:**
- Delete: `server/app/static/index.html`, `app.js`, `styles.css`, `views/dashboard.js`, `views/camera.js`, `views/create-agent.js`, `vendor/alpine.min.js`, `vendor/pico.min.css`

- [ ] **Step 1: Verify the v2 tree is loading correctly**

Hit the running server's `/`. Confirm the dark-themed shell renders.

- [ ] **Step 2: Delete the old files**

```bash
cd /root/timelapse
git rm server/app/static/index.html
git rm server/app/static/app.js
git rm server/app/static/styles.css
git rm server/app/static/views/dashboard.js
git rm server/app/static/views/camera.js
git rm server/app/static/views/create-agent.js
git rm server/app/static/vendor/alpine.min.js
git rm server/app/static/vendor/pico.min.css
rmdir server/app/static/views server/app/static/vendor 2>/dev/null || true
```

- [ ] **Step 3: Run server tests to confirm nothing else referenced these paths**

Run: `pytest tests/server/test_ui_routes.py -v`
Expected: PASS.

If it fails because the test references `STATIC_DIR / "index.html"` directly, update it to `STATIC_DIR / "v2" / "index.html"`.

- [ ] **Step 4: Commit**

```bash
git commit -m "chore(ui): remove old Pico/Alpine static tree"
```

---

## WAVE 3 — Convergence & ship

### Task T13: Bump agent VERSION

**Files:**
- Modify: `agent/VERSION`

- [ ] **Step 1: Bump**

```bash
echo "0.8.0" > /root/timelapse/agent/VERSION
```

- [ ] **Step 2: Commit**

```bash
git add agent/VERSION
git commit -m "chore(agent): 0.8.0 — schedule modes + scene-light"
```

---

### Task T14: README update

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add a "Scheduling modes" subsection**

Find the "Web UI" or "Usage & API" section and add:

```markdown
### Scheduling modes

Each camera supports three capture-schedule modes:

- **Hours** — fixed start/end window in agent local time (`capture_hours = [start..end-1]`).
- **Daylight** — auto-derived from sunrise/sunset for the camera's `latitude`/`longitude`. Falls back to 06:00–20:00 when location isn't set.
- **Scene light** — capture only when the camera's pre-capture frame is bright enough. Configured by `light_threshold` (mean Y luminance, 0–255). The agent samples a 64×48 YUV thumbnail before each scheduled capture and skips if below threshold. The latest reading is reported as `current_light` in heartbeats and shown live in the UI.

A weekday gate (`schedule_days`, ISO weekdays 1=Mon..7=Sun) applies to all three modes.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: README — describe schedule modes (hours/daylight/scene)"
```

---

### Task T15: Final smoke test

- [ ] **Step 1: Full test run**

```bash
cd /root/timelapse
.venv-dev/bin/pytest -q
```

Expected: all green.

- [ ] **Step 2: Live UI walkthrough**

```bash
cd /root/timelapse
TIMELAPSE_DATA_DIR=./dev-data .venv-dev/bin/python -m uvicorn server.app.main:app --reload --port 8765
```

Open `http://localhost:8765/` in a browser and verify:
1. Dark sidebar renders with brand, dashboard/library nav, camera list, settings link, storage meter at bottom
2. Dashboard shows the four stat cards + hero camera + side rail
3. Click a camera → camera detail page with five tabs (overview/frames/schedule/renders/settings)
4. Schedule tab → switch between Daylight, Hours, Scene-light → save → reload → mode persists
5. Settings tab → change display name → save → sidebar updates
6. Library → New render → MP4 and GIF buttons present, WebM absent
7. Add agent wizard → step 1 form validation → cannot generate provisioning key without name

- [ ] **Step 3: Final commit if anything was tweaked during smoke**

If smoke uncovers issues, fix them and commit; otherwise this task is just verification.

---

## Self-review checklist

Run through the spec section by section and confirm each requirement maps to a task:

| Spec section | Tasks |
|---|---|
| `hours` mode + visual track | T1 (validation), T10 (UI) |
| `daylight` mode | T1 (validation), T7 (calc), T10 (UI) |
| `scene` mode | T1 (validation), T8 (capture), T2 (status reporting), T10 (UI) |
| `schedule_days` weekday gate | T1 (validation), T6 (agent gate), T10 (UI) |
| `current_light` status | T2 (server accept), T8 (agent report), T10 (UI display) |
| `signal_dbm` status | T2 (server accept), T9 (agent report), T10 (UI display) |
| `display_name` config | T1 (schema), T10 (UI settings tab) |
| `latitude`/`longitude` config | T1 (schema), T7 (use in calc) |
| `stats` block on listing | T3, T10 (UI consumes) |
| `DELETE /api/cameras/:id` | T4, T10 (settings tab UI uses) |
| GIF render | T5, T11 (UI) |
| System fonts only | T10 (Google Fonts link removed) |
| Old static tree gone | T12 |
| Plus README and version bump | T13, T14, T15 (final smoke) |
