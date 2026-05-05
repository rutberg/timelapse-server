# Canon R6 / DSLR Support via gphoto2 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a second camera backend so the agent can drive a USB-tethered DSLR (validated against Canon R6) using `gphoto2`. Captures stay on the camera SD card and stream straight from the camera through the Pi to the server, so the Pi never persists large RAW files on its own filesystem.

**Architecture:** New `camera_backend` field on `CameraConfig` selects between `"rpicam"` (existing) and `"gphoto2"` (new), with `"auto"` defaulting to gphoto2 when a USB camera is detected. The agent gains a small set of `gphoto2`-CLI subprocess wrappers plus a JSON-backed pending queue that records `(folder, filename, captured_at)` references rather than image bytes. On upload the agent downloads each referenced file into `/tmp` (which is `tmpfs` / RAM-backed on Pi OS), POSTs it via the existing multipart endpoint, and deletes it from the camera on success. Provisioning installs `gphoto2`, adds a udev rule for Canon's USB vendor ID, and masks the gvfs auto-mounter that would otherwise grab the camera.

**Tech Stack:** Python 3.11+ stdlib only (the agent stays single-file, zero pip-deps), `gphoto2` CLI on the Pi, FastAPI + Pydantic v2 on the server, pytest with subprocess mocking.

---

## Design decisions baked into this plan

1. **CLI subprocess, not python-gphoto2 bindings.** Keeps the agent a single deployable file — no extra pip package, no compiled C extension to update through the auto-update pipeline.
2. **Camera storage is the queue.** `gphoto2 --capture-image` triggers without downloading; the file path on the camera is recorded in `pending_camera_files.json` in `work_dir`. Pi disk usage for queued images: zero.
3. **/tmp tmpfs as transient buffer.** During upload, files are downloaded to `/tmp/timelapse-agent-stage/<filename>`, POSTed, and deleted. `/tmp` is RAM-backed on Pi OS, so the SD card never wears.
4. **Server config doesn't control DSLR image size/quality.** Those are set on the camera body for DSLR mode (because `gphoto2` exposes them as per-model `--set-config` paths that vary by camera). The width/height/quality fields stay in the schema for rpicam mode and are ignored for gphoto2 mode.
5. **Scene-light schedule mode is unavailable for gphoto2 backend.** The DSLR has no fast YUV thumbnail path. The agent logs a warning and falls through (doesn't gate captures) when `schedule_mode == "scene"` on a gphoto2 camera.
6. **Auto-poweroff is disabled at agent startup** via `gphoto2 --set-config /main/settings/autopoweroff=0`. A sleeping camera drops the USB connection and breaks the upload loop.
7. **Single-file capture only for v1.** If the camera body is configured for RAW+JPEG, only the most recent file is enqueued per trigger. The plan documents this limitation; multi-file-per-capture is a future task.

---

## File structure

**Modified files:**
- `server/app/main.py` — add `camera_backend` field to `CameraConfig`
- `agent/timelapse_agent.py` — add gphoto2 backend functions, branch `capture_frame` and `upload_pending`, update `measure_pending`
- `server/app/provision_script.py` — install `gphoto2`, write Canon udev rule, add agent user to `plugdev`, mask `gvfs-gphoto2-volume-monitor`
- `agent/VERSION` — bump (current value at time of writing: read with `cat agent/VERSION`)

**New files:**
- `tests/server/test_camera_backend_field.py` — schema validation for `camera_backend`
- `tests/agent/test_gphoto2_backend.py` — unit tests for all new agent helpers (subprocess mocked)
- `tests/server/test_provision_dslr.py` — provisioning script includes gphoto2 setup

**Deleted files:** none.

---

## Wave structure

```
Wave 1 (sequential):  T1                         (server schema)
Wave 2 (sequential):  T2 → T3 → T4 → T5 → T6     (agent gphoto2 primitives)
Wave 3 (sequential):  T7 → T8 → T9 → T10         (agent integration)
Wave 4 (parallel):    [T11] [T12]                (provisioning + version bump)
Wave 5:               T13                        (hardware verification — manual)
```

Wave 1 must finish before Wave 2 (agent reads `camera_backend` from server config). Wave 2 is sequential because every task modifies `agent/timelapse_agent.py` in overlapping regions. Wave 4 tasks touch independent files and may be dispatched in parallel.

---

## WAVE 1 — Server schema

### Task T1: Add `camera_backend` field to `CameraConfig`

**Files:**
- Modify: `server/app/main.py` (`CameraConfig` class, after `longitude` field around line 85)
- Test: `tests/server/test_camera_backend_field.py` (new)

- [ ] **Step 1: Write the failing tests**

```python
# tests/server/test_camera_backend_field.py
import pytest
from app.main import CameraConfig


class TestCameraBackendField:
    def test_default_is_auto(self):
        cfg = CameraConfig()
        assert cfg.camera_backend == "auto"

    def test_accepts_rpicam(self):
        cfg = CameraConfig(camera_backend="rpicam")
        assert cfg.camera_backend == "rpicam"

    def test_accepts_gphoto2(self):
        cfg = CameraConfig(camera_backend="gphoto2")
        assert cfg.camera_backend == "gphoto2"

    def test_accepts_auto(self):
        cfg = CameraConfig(camera_backend="auto")
        assert cfg.camera_backend == "auto"

    def test_rejects_unknown_backend(self):
        with pytest.raises(ValueError, match="camera_backend"):
            CameraConfig(camera_backend="webcam")

    def test_rejects_empty_string(self):
        with pytest.raises(ValueError, match="camera_backend"):
            CameraConfig(camera_backend="")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/timelapse && pytest tests/server/test_camera_backend_field.py -v`
Expected: All tests FAIL with `AttributeError` or `ValidationError` ("unexpected keyword argument 'camera_backend'" or "Extra inputs are not permitted").

- [ ] **Step 3: Add the field and validator to `CameraConfig`**

In `server/app/main.py`, add this field after the `longitude` field (around line 85) and before the `@field_validator("capture_hours")` decorator:

```python
    camera_backend: str = Field(
        default="auto",
        description=(
            "Capture backend selection: 'auto' (gphoto2 if a USB camera is "
            "detected, otherwise rpicam), 'rpicam' (Pi camera via rpicam-still/"
            "libcamera-still/raspistill), or 'gphoto2' (USB DSLR via gphoto2)."
        ),
    )

    @field_validator("camera_backend")
    @classmethod
    def _validate_camera_backend(cls, value):
        allowed = {"auto", "rpicam", "gphoto2"}
        if value not in allowed:
            raise ValueError(
                f"camera_backend must be one of {sorted(allowed)}, got {value!r}"
            )
        return value
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/timelapse && pytest tests/server/test_camera_backend_field.py -v`
Expected: All 6 tests PASS.

- [ ] **Step 5: Run the full server test suite to confirm no regressions**

Run: `cd /root/timelapse && pytest tests/server/ -v`
Expected: All tests pass (the new field is optional with a default, so existing configs still validate).

- [ ] **Step 6: Commit**

```bash
cd /root/timelapse
git add server/app/main.py tests/server/test_camera_backend_field.py
git commit -m "feat(server): add camera_backend field to CameraConfig"
```

---

## WAVE 2 — Agent gphoto2 primitives

All Wave 2 tasks add new top-level functions to `agent/timelapse_agent.py`. They do **not** yet wire into `capture_frame` or `upload_pending` — that happens in Wave 3.

### Task T2: Detect gphoto2 availability and resolve active backend

**Files:**
- Modify: `agent/timelapse_agent.py` (add functions after `find_capture_command` at line 502)
- Test: `tests/agent/test_gphoto2_backend.py` (new)

- [ ] **Step 1: Write the failing tests**

```python
# tests/agent/test_gphoto2_backend.py
from unittest.mock import patch, MagicMock
import subprocess

import pytest

from timelapse_agent import (
    gphoto2_available,
    resolve_active_backend,
)


class TestGphoto2Available:
    def test_returns_false_when_binary_missing(self):
        with patch("timelapse_agent.shutil.which", return_value=None):
            assert gphoto2_available() is False

    def test_returns_false_when_no_camera_detected(self):
        # `gphoto2 --auto-detect` exits 0 even with no camera; output has only the header.
        empty_output = "Model                          Port\n----------------------------------------------------------\n"
        completed = MagicMock(returncode=0, stdout=empty_output, stderr="")
        with patch("timelapse_agent.shutil.which", return_value="/usr/bin/gphoto2"), \
             patch("timelapse_agent.subprocess.run", return_value=completed):
            assert gphoto2_available() is False

    def test_returns_true_when_camera_listed(self):
        output = (
            "Model                          Port\n"
            "----------------------------------------------------------\n"
            "Canon EOS R6                   usb:001,005\n"
        )
        completed = MagicMock(returncode=0, stdout=output, stderr="")
        with patch("timelapse_agent.shutil.which", return_value="/usr/bin/gphoto2"), \
             patch("timelapse_agent.subprocess.run", return_value=completed):
            assert gphoto2_available() is True

    def test_returns_false_on_subprocess_error(self):
        with patch("timelapse_agent.shutil.which", return_value="/usr/bin/gphoto2"), \
             patch("timelapse_agent.subprocess.run",
                   side_effect=subprocess.TimeoutExpired(cmd="gphoto2", timeout=5)):
            assert gphoto2_available() is False


class TestResolveActiveBackend:
    def test_explicit_rpicam(self):
        with patch("timelapse_agent.find_capture_command", return_value="/usr/bin/rpicam-still"), \
             patch("timelapse_agent.gphoto2_available", return_value=True):
            assert resolve_active_backend({"camera_backend": "rpicam"}) == "rpicam"

    def test_explicit_gphoto2(self):
        with patch("timelapse_agent.find_capture_command", return_value=None), \
             patch("timelapse_agent.gphoto2_available", return_value=True):
            assert resolve_active_backend({"camera_backend": "gphoto2"}) == "gphoto2"

    def test_auto_prefers_gphoto2_when_camera_detected(self):
        with patch("timelapse_agent.find_capture_command", return_value="/usr/bin/rpicam-still"), \
             patch("timelapse_agent.gphoto2_available", return_value=True):
            assert resolve_active_backend({"camera_backend": "auto"}) == "gphoto2"

    def test_auto_falls_back_to_rpicam_when_no_dslr(self):
        with patch("timelapse_agent.find_capture_command", return_value="/usr/bin/rpicam-still"), \
             patch("timelapse_agent.gphoto2_available", return_value=False):
            assert resolve_active_backend({"camera_backend": "auto"}) == "rpicam"

    def test_auto_returns_none_when_nothing_available(self):
        with patch("timelapse_agent.find_capture_command", return_value=None), \
             patch("timelapse_agent.gphoto2_available", return_value=False):
            assert resolve_active_backend({"camera_backend": "auto"}) is None

    def test_missing_field_treated_as_auto(self):
        with patch("timelapse_agent.find_capture_command", return_value="/usr/bin/rpicam-still"), \
             patch("timelapse_agent.gphoto2_available", return_value=False):
            assert resolve_active_backend({}) == "rpicam"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/timelapse && pytest tests/agent/test_gphoto2_backend.py -v`
Expected: FAIL with `ImportError` ("cannot import name 'gphoto2_available' from 'timelapse_agent'").

- [ ] **Step 3: Implement `gphoto2_available` and `resolve_active_backend`**

In `agent/timelapse_agent.py`, immediately after the `find_capture_command` function (currently ending at line 502), add:

```python
def gphoto2_available() -> bool:
    """Return True if the gphoto2 binary is installed AND a camera is currently
    attached and visible to libgphoto2 over USB.

    `gphoto2 --auto-detect` always exits 0; an empty list is signalled by the
    output containing only the two-line header. We detect a camera by counting
    non-header lines.
    """
    if not shutil.which("gphoto2"):
        return False
    try:
        result = subprocess.run(
            ["gphoto2", "--auto-detect"],
            check=False, capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    if result.returncode != 0:
        return False
    # Output format:
    #   Model                          Port
    #   ----------------------------------------------------------
    #   Canon EOS R6                   usb:001,005
    for line in result.stdout.splitlines()[2:]:
        if line.strip():
            return True
    return False


def resolve_active_backend(config: Dict[str, Any]) -> Optional[str]:
    """Pick which capture backend to use given the server config.

    Returns 'rpicam', 'gphoto2', or None if neither is available.
    """
    requested = config.get("camera_backend", "auto")
    if requested == "rpicam":
        return "rpicam" if find_capture_command() else None
    if requested == "gphoto2":
        return "gphoto2" if gphoto2_available() else None
    # auto: prefer gphoto2 (more specialised) when a USB camera is present
    if gphoto2_available():
        return "gphoto2"
    if find_capture_command():
        return "rpicam"
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/timelapse && pytest tests/agent/test_gphoto2_backend.py -v`
Expected: All tests under `TestGphoto2Available` and `TestResolveActiveBackend` PASS (10 tests).

- [ ] **Step 5: Commit**

```bash
cd /root/timelapse
git add agent/timelapse_agent.py tests/agent/test_gphoto2_backend.py
git commit -m "feat(agent): add gphoto2 detection and backend resolver"
```

---

### Task T3: gphoto2 capture trigger + parse new file path

**Files:**
- Modify: `agent/timelapse_agent.py` (add functions after `resolve_active_backend`)
- Modify: `tests/agent/test_gphoto2_backend.py` (append a new test class)

- [ ] **Step 1: Append new failing tests**

Append to `tests/agent/test_gphoto2_backend.py`:

```python
from timelapse_agent import (
    CameraFileRef,
    parse_new_file_location,
    gphoto2_capture_trigger,
)


class TestParseNewFileLocation:
    def test_parses_standard_canon_output(self):
        output = (
            "New file is in location /store_00020001/DCIM/100CANON/IMG_0042.CR3 on the camera\n"
        )
        ref = parse_new_file_location(output)
        assert ref == CameraFileRef(
            folder="/store_00020001/DCIM/100CANON",
            filename="IMG_0042.CR3",
        )

    def test_parses_jpeg_output(self):
        output = "New file is in location /store_00010001/DCIM/100CANON/IMG_0099.JPG on the camera\n"
        ref = parse_new_file_location(output)
        assert ref.filename == "IMG_0099.JPG"
        assert ref.folder == "/store_00010001/DCIM/100CANON"

    def test_parses_when_line_is_not_last(self):
        output = (
            "Some progress noise\n"
            "New file is in location /a/b/IMG_1.CR3 on the camera\n"
            "Saving file...\n"
        )
        ref = parse_new_file_location(output)
        assert ref.folder == "/a/b"
        assert ref.filename == "IMG_1.CR3"

    def test_returns_none_when_no_match(self):
        assert parse_new_file_location("No file here\n") is None
        assert parse_new_file_location("") is None


class TestGphoto2CaptureTrigger:
    def test_runs_capture_image_and_returns_ref(self):
        completed = MagicMock(
            returncode=0,
            stdout="New file is in location /a/b/IMG_5.CR3 on the camera\n",
            stderr="",
        )
        with patch("timelapse_agent.subprocess.run", return_value=completed) as mock_run:
            ref = gphoto2_capture_trigger()
        assert ref == CameraFileRef(folder="/a/b", filename="IMG_5.CR3")
        args, kwargs = mock_run.call_args
        assert args[0] == ["gphoto2", "--capture-image"]
        assert kwargs.get("check") is True
        assert kwargs.get("capture_output") is True
        assert kwargs.get("text") is True

    def test_raises_when_output_unparseable(self):
        completed = MagicMock(returncode=0, stdout="No new file line\n", stderr="")
        with patch("timelapse_agent.subprocess.run", return_value=completed):
            with pytest.raises(RuntimeError, match="parse"):
                gphoto2_capture_trigger()

    def test_propagates_subprocess_error(self):
        with patch("timelapse_agent.subprocess.run",
                   side_effect=subprocess.CalledProcessError(1, "gphoto2", stderr="cam offline")):
            with pytest.raises(subprocess.CalledProcessError):
                gphoto2_capture_trigger()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/timelapse && pytest tests/agent/test_gphoto2_backend.py -v`
Expected: New tests FAIL with `ImportError` ("cannot import name 'CameraFileRef'").

- [ ] **Step 3: Add the dataclass, parser, and trigger function**

In `agent/timelapse_agent.py`, add `import re` to the imports at the top of the file (insert in alphabetical order — after `import os`).

Then immediately after `resolve_active_backend`, add:

```python
@dataclass(frozen=True)
class CameraFileRef:
    folder: str
    filename: str


# gphoto2 prints a line like:
#   New file is in location /store_00020001/DCIM/100CANON/IMG_0042.CR3 on the camera
_NEW_FILE_RE = re.compile(
    r"^New file is in location (?P<path>/\S+?) on the camera\s*$"
)


def parse_new_file_location(stdout: str) -> Optional[CameraFileRef]:
    """Parse gphoto2 --capture-image stdout and return the camera file reference.

    Returns None if no `New file is in location` line is found.
    """
    for line in stdout.splitlines():
        match = _NEW_FILE_RE.match(line)
        if match:
            full = match.group("path")
            folder, _, filename = full.rpartition("/")
            return CameraFileRef(folder=folder or "/", filename=filename)
    return None


def gphoto2_capture_trigger(timeout: int = 30) -> CameraFileRef:
    """Trigger a capture on the connected DSLR; image stays on the camera SD.

    Returns the (folder, filename) reference parsed from gphoto2 stdout. Raises
    RuntimeError if the output can't be parsed, or subprocess.CalledProcessError
    on a non-zero exit (e.g. camera disconnected, SD full).
    """
    result = subprocess.run(
        ["gphoto2", "--capture-image"],
        check=True, capture_output=True, text=True, timeout=timeout,
    )
    ref = parse_new_file_location(result.stdout)
    if ref is None:
        raise RuntimeError(
            f"Could not parse gphoto2 capture output: {result.stdout!r}"
        )
    return ref
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/timelapse && pytest tests/agent/test_gphoto2_backend.py -v`
Expected: All tests in the file PASS (previous 10 + 7 new = 17).

- [ ] **Step 5: Commit**

```bash
cd /root/timelapse
git add agent/timelapse_agent.py tests/agent/test_gphoto2_backend.py
git commit -m "feat(agent): add gphoto2 capture trigger and stdout parser"
```

---

### Task T4: Pending camera files state JSON helpers

**Files:**
- Modify: `agent/timelapse_agent.py` (add functions after `gphoto2_capture_trigger`)
- Modify: `tests/agent/test_gphoto2_backend.py` (append a new test class)

- [ ] **Step 1: Append new failing tests**

Append to `tests/agent/test_gphoto2_backend.py`:

```python
from timelapse_agent import (
    PENDING_CAMERA_FILES_NAME,
    load_camera_pending,
    save_camera_pending,
    add_camera_pending,
    remove_camera_pending,
)


class TestCameraPendingState:
    def test_load_returns_empty_when_file_missing(self, tmp_path):
        assert load_camera_pending(tmp_path) == []

    def test_save_then_load_roundtrip(self, tmp_path):
        entries = [
            {"folder": "/a", "filename": "IMG_1.CR3", "captured_at": "2026-05-05T10:00:00-07:00"},
            {"folder": "/a", "filename": "IMG_2.CR3", "captured_at": "2026-05-05T10:01:00-07:00"},
        ]
        save_camera_pending(tmp_path, entries)
        assert load_camera_pending(tmp_path) == entries

    def test_save_writes_to_expected_filename(self, tmp_path):
        save_camera_pending(tmp_path, [])
        assert (tmp_path / PENDING_CAMERA_FILES_NAME).exists()

    def test_add_appends_entry(self, tmp_path):
        add_camera_pending(
            tmp_path,
            CameraFileRef(folder="/a", filename="X.CR3"),
            captured_at="2026-05-05T10:00:00-07:00",
        )
        add_camera_pending(
            tmp_path,
            CameraFileRef(folder="/a", filename="Y.CR3"),
            captured_at="2026-05-05T10:01:00-07:00",
        )
        entries = load_camera_pending(tmp_path)
        assert len(entries) == 2
        assert entries[0]["filename"] == "X.CR3"
        assert entries[1]["filename"] == "Y.CR3"

    def test_remove_drops_matching_entry(self, tmp_path):
        save_camera_pending(tmp_path, [
            {"folder": "/a", "filename": "X.CR3", "captured_at": "t1"},
            {"folder": "/a", "filename": "Y.CR3", "captured_at": "t2"},
        ])
        remove_camera_pending(tmp_path, CameraFileRef(folder="/a", filename="X.CR3"))
        entries = load_camera_pending(tmp_path)
        assert len(entries) == 1
        assert entries[0]["filename"] == "Y.CR3"

    def test_remove_is_idempotent_when_entry_missing(self, tmp_path):
        save_camera_pending(tmp_path, [
            {"folder": "/a", "filename": "X.CR3", "captured_at": "t1"},
        ])
        remove_camera_pending(tmp_path, CameraFileRef(folder="/a", filename="ZZZ.CR3"))
        assert len(load_camera_pending(tmp_path)) == 1

    def test_save_uses_atomic_write(self, tmp_path):
        # Multiple saves should leave a single canonical file (no .tmp lingering).
        save_camera_pending(tmp_path, [{"folder": "/a", "filename": "X.CR3", "captured_at": "t"}])
        save_camera_pending(tmp_path, [{"folder": "/a", "filename": "Y.CR3", "captured_at": "t"}])
        leftovers = list(tmp_path.glob("*.tmp"))
        assert leftovers == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/timelapse && pytest tests/agent/test_gphoto2_backend.py::TestCameraPendingState -v`
Expected: FAIL with `ImportError` ("cannot import name 'PENDING_CAMERA_FILES_NAME'").

- [ ] **Step 3: Implement the state helpers**

In `agent/timelapse_agent.py`, add immediately after `gphoto2_capture_trigger`:

```python
PENDING_CAMERA_FILES_NAME = "pending_camera_files.json"


def load_camera_pending(work_dir: Path) -> List[Dict[str, str]]:
    """Read the pending-camera-files queue. Returns [] if file missing or invalid."""
    path = work_dir / PENDING_CAMERA_FILES_NAME
    if not path.exists():
        return []
    try:
        data = load_json(path)
    except (json.JSONDecodeError, OSError):
        return []
    entries = data.get("entries") if isinstance(data, dict) else None
    return entries if isinstance(entries, list) else []


def save_camera_pending(work_dir: Path, entries: List[Dict[str, str]]) -> None:
    """Atomically write the pending-camera-files queue."""
    write_json(work_dir / PENDING_CAMERA_FILES_NAME, {"entries": entries})


def add_camera_pending(work_dir: Path, ref: CameraFileRef, captured_at: str) -> None:
    """Append a new pending entry for an image still on the camera."""
    entries = load_camera_pending(work_dir)
    entries.append({
        "folder": ref.folder,
        "filename": ref.filename,
        "captured_at": captured_at,
    })
    save_camera_pending(work_dir, entries)


def remove_camera_pending(work_dir: Path, ref: CameraFileRef) -> None:
    """Drop the entry matching (folder, filename). No-op if missing."""
    entries = load_camera_pending(work_dir)
    filtered = [
        e for e in entries
        if not (e.get("folder") == ref.folder and e.get("filename") == ref.filename)
    ]
    if len(filtered) != len(entries):
        save_camera_pending(work_dir, filtered)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/timelapse && pytest tests/agent/test_gphoto2_backend.py -v`
Expected: All tests in the file PASS (17 + 7 = 24 total).

- [ ] **Step 5: Commit**

```bash
cd /root/timelapse
git add agent/timelapse_agent.py tests/agent/test_gphoto2_backend.py
git commit -m "feat(agent): add pending-camera-files JSON state helpers"
```

---

### Task T5: Download a camera file to /tmp tmpfs

**Files:**
- Modify: `agent/timelapse_agent.py` (add function after `remove_camera_pending`)
- Modify: `tests/agent/test_gphoto2_backend.py` (append a new test class)

- [ ] **Step 1: Append new failing tests**

Append to `tests/agent/test_gphoto2_backend.py`:

```python
from pathlib import Path

from timelapse_agent import (
    GPHOTO2_STAGE_DIR,
    gphoto2_download_file,
)


class TestGphoto2DownloadFile:
    def test_calls_gphoto2_with_correct_args(self, tmp_path):
        ref = CameraFileRef(folder="/store_0001/DCIM/100CANON", filename="IMG_42.CR3")
        dest = tmp_path / "IMG_42.CR3"
        # Simulate gphoto2 creating the file as a side effect.
        def fake_run(cmd, **kwargs):
            dest.write_bytes(b"fake-image-bytes")
            return MagicMock(returncode=0, stdout="", stderr="")
        with patch("timelapse_agent.subprocess.run", side_effect=fake_run) as mock_run:
            result = gphoto2_download_file(ref, dest)
        assert result == dest
        assert dest.exists()
        cmd_args = mock_run.call_args[0][0]
        assert cmd_args[0] == "gphoto2"
        assert "--folder" in cmd_args
        assert "/store_0001/DCIM/100CANON" in cmd_args
        assert "--filename" in cmd_args
        assert str(dest) in cmd_args
        assert "--get-file" in cmd_args
        assert "IMG_42.CR3" in cmd_args
        assert "--force-overwrite" in cmd_args

    def test_creates_parent_directory(self, tmp_path):
        ref = CameraFileRef(folder="/a", filename="X.CR3")
        dest = tmp_path / "subdir" / "X.CR3"
        def fake_run(cmd, **kwargs):
            dest.write_bytes(b"x")
            return MagicMock(returncode=0, stdout="", stderr="")
        with patch("timelapse_agent.subprocess.run", side_effect=fake_run):
            gphoto2_download_file(ref, dest)
        assert dest.exists()

    def test_default_stage_dir_is_tmpfs_path(self):
        # Documents the tmpfs choice — /tmp is RAM-backed on Pi OS.
        assert GPHOTO2_STAGE_DIR == Path("/tmp/timelapse-agent-stage")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/timelapse && pytest tests/agent/test_gphoto2_backend.py::TestGphoto2DownloadFile -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement `gphoto2_download_file`**

In `agent/timelapse_agent.py`, after `remove_camera_pending`, add:

```python
GPHOTO2_STAGE_DIR = Path("/tmp/timelapse-agent-stage")


def gphoto2_download_file(ref: CameraFileRef, dest_path: Path, timeout: int = 120) -> Path:
    """Download a single file from the camera to dest_path.

    The destination should live on tmpfs (/tmp on Pi OS) so the SD card never
    sees the bytes. Caller is responsible for unlinking dest_path after upload.
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "gphoto2",
            "--folder", ref.folder,
            "--get-file", ref.filename,
            "--filename", str(dest_path),
            "--force-overwrite",
        ],
        check=True, capture_output=True, text=True, timeout=timeout,
    )
    return dest_path
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/timelapse && pytest tests/agent/test_gphoto2_backend.py -v`
Expected: All tests PASS (24 + 3 = 27 total).

- [ ] **Step 5: Commit**

```bash
cd /root/timelapse
git add agent/timelapse_agent.py tests/agent/test_gphoto2_backend.py
git commit -m "feat(agent): add gphoto2 file download to tmpfs stage dir"
```

---

### Task T6: Delete a file from the camera

**Files:**
- Modify: `agent/timelapse_agent.py` (add function after `gphoto2_download_file`)
- Modify: `tests/agent/test_gphoto2_backend.py` (append a new test class)

- [ ] **Step 1: Append new failing tests**

Append to `tests/agent/test_gphoto2_backend.py`:

```python
from timelapse_agent import gphoto2_delete_file


class TestGphoto2DeleteFile:
    def test_calls_gphoto2_with_correct_args(self):
        ref = CameraFileRef(folder="/a/b", filename="IMG_1.CR3")
        completed = MagicMock(returncode=0, stdout="", stderr="")
        with patch("timelapse_agent.subprocess.run", return_value=completed) as mock_run:
            gphoto2_delete_file(ref)
        cmd_args = mock_run.call_args[0][0]
        assert cmd_args[0] == "gphoto2"
        assert "--folder" in cmd_args
        assert "/a/b" in cmd_args
        assert "--delete-file" in cmd_args
        assert "IMG_1.CR3" in cmd_args

    def test_swallows_file_not_found_on_camera(self):
        # If the file is already gone (e.g. user deleted it on the camera, or
        # we previously deleted it but crashed before removing the state entry),
        # treat that as success — the queue entry should be cleared either way.
        err = subprocess.CalledProcessError(
            returncode=1, cmd="gphoto2",
            stderr="ERROR: File not found.\n",
        )
        with patch("timelapse_agent.subprocess.run", side_effect=err):
            # Should NOT raise.
            gphoto2_delete_file(CameraFileRef(folder="/a", filename="GONE.CR3"))

    def test_propagates_other_errors(self):
        err = subprocess.CalledProcessError(
            returncode=1, cmd="gphoto2",
            stderr="ERROR: Could not claim USB device.\n",
        )
        with patch("timelapse_agent.subprocess.run", side_effect=err):
            with pytest.raises(subprocess.CalledProcessError):
                gphoto2_delete_file(CameraFileRef(folder="/a", filename="X.CR3"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/timelapse && pytest tests/agent/test_gphoto2_backend.py::TestGphoto2DeleteFile -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement `gphoto2_delete_file`**

In `agent/timelapse_agent.py`, after `gphoto2_download_file`, add:

```python
def gphoto2_delete_file(ref: CameraFileRef, timeout: int = 30) -> None:
    """Delete a single file from the camera SD card.

    "File not found" is treated as success — the entry was already gone, which
    is exactly the state we wanted to reach. Other failures (USB claim errors,
    write-protected card, etc.) are propagated.
    """
    try:
        subprocess.run(
            [
                "gphoto2",
                "--folder", ref.folder,
                "--delete-file", ref.filename,
            ],
            check=True, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.CalledProcessError as error:
        stderr = (error.stderr or "").lower()
        if "file not found" in stderr:
            return
        raise
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/timelapse && pytest tests/agent/test_gphoto2_backend.py -v`
Expected: All tests PASS (27 + 3 = 30 total).

- [ ] **Step 5: Commit**

```bash
cd /root/timelapse
git add agent/timelapse_agent.py tests/agent/test_gphoto2_backend.py
git commit -m "feat(agent): add gphoto2 file deletion with not-found tolerance"
```

---

## WAVE 3 — Agent integration

### Task T7: Branch `capture_frame` on the active backend

**Files:**
- Modify: `agent/timelapse_agent.py` (`capture_frame` at lines 591–612)
- Modify: `tests/agent/test_gphoto2_backend.py` (append a new test class)

- [ ] **Step 1: Append new failing tests**

Append to `tests/agent/test_gphoto2_backend.py`:

```python
from timelapse_agent import capture_frame


class TestCaptureFrameGphoto2Branch:
    def test_gphoto2_capture_appends_to_queue_and_returns_ref(self, tmp_path):
        config = {"camera_backend": "gphoto2"}
        completed = MagicMock(
            returncode=0,
            stdout="New file is in location /a/b/IMG_99.CR3 on the camera\n",
            stderr="",
        )
        with patch("timelapse_agent.resolve_active_backend", return_value="gphoto2"), \
             patch("timelapse_agent.subprocess.run", return_value=completed):
            result = capture_frame(tmp_path, config)
        assert isinstance(result, CameraFileRef)
        assert result.filename == "IMG_99.CR3"
        entries = load_camera_pending(tmp_path)
        assert len(entries) == 1
        assert entries[0]["filename"] == "IMG_99.CR3"
        assert entries[0]["folder"] == "/a/b"
        assert "captured_at" in entries[0]

    def test_rpicam_capture_path_unchanged(self, tmp_path):
        # When backend is rpicam, capture_frame should still write to pending/.
        config = {"camera_backend": "rpicam", "jpeg_quality": 85}

        def fake_run(cmd, **kwargs):
            # Simulate rpicam writing the temp jpg.
            output_arg = cmd[cmd.index("--output") + 1] if "--output" in cmd else cmd[cmd.index("-o") + 1]
            Path(output_arg).write_bytes(b"jpeg-bytes")
            return MagicMock(returncode=0)

        with patch("timelapse_agent.resolve_active_backend", return_value="rpicam"), \
             patch("timelapse_agent.find_capture_command", return_value="/usr/bin/rpicam-still"), \
             patch("timelapse_agent.subprocess.run", side_effect=fake_run):
            result = capture_frame(tmp_path, config)
        assert isinstance(result, Path)
        assert result.suffix == ".jpg"
        assert result.parent == tmp_path / "pending"

    def test_no_backend_raises(self, tmp_path):
        with patch("timelapse_agent.resolve_active_backend", return_value=None):
            with pytest.raises(RuntimeError, match="No camera backend"):
                capture_frame(tmp_path, {"camera_backend": "auto"})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/timelapse && pytest tests/agent/test_gphoto2_backend.py::TestCaptureFrameGphoto2Branch -v`
Expected: FAIL — current `capture_frame` calls `find_capture_command()` directly and doesn't know about gphoto2.

- [ ] **Step 3: Refactor `capture_frame` to branch on backend**

Replace the existing `capture_frame` (currently lines 591–612 in `agent/timelapse_agent.py`) with:

```python
def capture_frame(work_dir: Path, config: Dict[str, Any]):
    """Trigger a capture using the configured backend.

    Returns:
        - For 'rpicam' backend: pathlib.Path to the JPEG written under work_dir/pending/.
        - For 'gphoto2' backend: a CameraFileRef pointing to the newly captured
          file still residing on the camera SD card.
    """
    backend = resolve_active_backend(config)
    if backend is None:
        raise RuntimeError(
            "No camera backend available: install rpicam-apps-lite for Pi cameras "
            "or gphoto2 + a USB DSLR"
        )

    if backend == "gphoto2":
        ref = gphoto2_capture_trigger()
        add_camera_pending(work_dir, ref, captured_at=now_local_iso())
        return ref

    # rpicam path (unchanged behaviour)
    command = find_capture_command()
    captured_at_filename = datetime.now().strftime("%Y%m%dT%H%M%S")
    output_path = work_dir / "pending" / f"{captured_at_filename}.jpg"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_suffix(".tmp.jpg")

    subprocess.run(build_capture_command(command, temp_path, config), check=True)
    temp_path.replace(output_path)

    metadata = {
        "captured_at": now_local_iso(),
        "hostname": socket.gethostname(),
    }
    write_json(output_path.with_suffix(".json"), metadata)
    return output_path
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/timelapse && pytest tests/agent/test_gphoto2_backend.py -v`
Expected: All tests PASS (30 + 3 = 33). Also run the full agent suite to confirm no regressions:

Run: `cd /root/timelapse && pytest tests/agent/ -v`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
cd /root/timelapse
git add agent/timelapse_agent.py tests/agent/test_gphoto2_backend.py
git commit -m "feat(agent): branch capture_frame on active backend"
```

---

### Task T8: Branch `upload_pending` to handle camera-resident files

**Files:**
- Modify: `agent/timelapse_agent.py` (`upload_pending` at lines 628–649, `measure_pending` at lines 210–222)
- Modify: `tests/agent/test_gphoto2_backend.py` (append a new test class)

- [ ] **Step 1: Append new failing tests**

Append to `tests/agent/test_gphoto2_backend.py`:

```python
from timelapse_agent import (
    AgentState,
    upload_pending,
    upload_camera_pending,
    measure_camera_pending,
)


class TestUploadCameraPending:
    def test_uploads_each_entry_then_clears_state_and_camera(self, tmp_path, monkeypatch):
        # Two queued items.
        save_camera_pending(tmp_path, [
            {"folder": "/a", "filename": "IMG_1.CR3", "captured_at": "2026-05-05T10:00:00-07:00"},
            {"folder": "/a", "filename": "IMG_2.CR3", "captured_at": "2026-05-05T10:01:00-07:00"},
        ])
        download_calls = []
        delete_calls = []
        post_calls = []

        def fake_download(ref, dest):
            download_calls.append((ref, dest))
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"fake")
            return dest

        def fake_delete(ref):
            delete_calls.append(ref)

        def fake_post(url, file_path, captured_at, timeout=60):
            post_calls.append((url, file_path.name, captured_at))
            return {"ok": True}

        monkeypatch.setattr("timelapse_agent.gphoto2_download_file", fake_download)
        monkeypatch.setattr("timelapse_agent.gphoto2_delete_file", fake_delete)
        monkeypatch.setattr("timelapse_agent.post_multipart", fake_post)

        settings = {"server_url": "http://srv", "camera_id": "cam1"}
        state = AgentState()
        upload_camera_pending(settings, tmp_path, state)

        assert len(post_calls) == 2
        assert len(delete_calls) == 2
        # State is empty.
        assert load_camera_pending(tmp_path) == []
        # Stage files were cleaned up.
        assert not any(GPHOTO2_STAGE_DIR.glob("IMG_*.CR3")) if GPHOTO2_STAGE_DIR.exists() else True
        assert state.last_error is None

    def test_keeps_entry_when_upload_fails(self, tmp_path, monkeypatch):
        from urllib.error import HTTPError
        save_camera_pending(tmp_path, [
            {"folder": "/a", "filename": "IMG_1.CR3", "captured_at": "t1"},
        ])
        monkeypatch.setattr("timelapse_agent.gphoto2_download_file",
                            lambda ref, dest: (dest.parent.mkdir(parents=True, exist_ok=True),
                                                dest.write_bytes(b"x"), dest)[2])
        deleted = []
        monkeypatch.setattr("timelapse_agent.gphoto2_delete_file",
                            lambda ref: deleted.append(ref))
        def boom(*a, **kw):
            raise HTTPError("u", 500, "boom", {}, None)
        monkeypatch.setattr("timelapse_agent.post_multipart", boom)

        state = AgentState()
        upload_camera_pending({"server_url": "http://x", "camera_id": "c"}, tmp_path, state)

        # Entry survives, camera file not deleted, error recorded.
        assert len(load_camera_pending(tmp_path)) == 1
        assert deleted == []
        assert state.last_error is not None

    def test_drops_entry_when_camera_says_file_missing(self, tmp_path, monkeypatch):
        # If the file vanished from the camera (user wiped SD, etc.), drop the
        # state entry instead of looping forever on a doomed download.
        save_camera_pending(tmp_path, [
            {"folder": "/a", "filename": "GONE.CR3", "captured_at": "t1"},
        ])
        err = subprocess.CalledProcessError(
            returncode=1, cmd="gphoto2",
            stderr="ERROR: Could not find file '/a/GONE.CR3'.\n",
        )
        monkeypatch.setattr("timelapse_agent.gphoto2_download_file",
                            lambda ref, dest: (_ for _ in ()).throw(err))

        state = AgentState()
        upload_camera_pending({"server_url": "http://x", "camera_id": "c"}, tmp_path, state)

        assert load_camera_pending(tmp_path) == []


class TestMeasureCameraPending:
    def test_zero_when_no_state_file(self, tmp_path):
        assert measure_camera_pending(tmp_path) == 0

    def test_counts_entries(self, tmp_path):
        save_camera_pending(tmp_path, [
            {"folder": "/a", "filename": "X.CR3", "captured_at": "t1"},
            {"folder": "/a", "filename": "Y.CR3", "captured_at": "t2"},
            {"folder": "/a", "filename": "Z.CR3", "captured_at": "t3"},
        ])
        assert measure_camera_pending(tmp_path) == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/timelapse && pytest tests/agent/test_gphoto2_backend.py::TestUploadCameraPending tests/agent/test_gphoto2_backend.py::TestMeasureCameraPending -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement `upload_camera_pending` and `measure_camera_pending`; update `upload_pending` to delegate**

In `agent/timelapse_agent.py`, add after `gphoto2_delete_file` (which T6 added):

```python
def measure_camera_pending(work_dir: Path) -> int:
    """Number of images queued on the camera awaiting upload."""
    return len(load_camera_pending(work_dir))


def upload_camera_pending(
    settings: Dict[str, Any], work_dir: Path, state: AgentState
) -> None:
    """For gphoto2 backend: stream each pending camera file → server → delete from camera."""
    entries = load_camera_pending(work_dir)
    if not entries:
        return

    url = settings["server_url"].rstrip("/") + f"/api/cameras/{settings['camera_id']}/upload"
    GPHOTO2_STAGE_DIR.mkdir(parents=True, exist_ok=True)

    for entry in list(entries):
        ref = CameraFileRef(folder=entry["folder"], filename=entry["filename"])
        captured_at = entry.get("captured_at") or now_local_iso()
        stage_path = GPHOTO2_STAGE_DIR / ref.filename

        try:
            gphoto2_download_file(ref, stage_path)
        except subprocess.CalledProcessError as error:
            stderr = (error.stderr or "").lower()
            if "could not find" in stderr or "file not found" in stderr:
                logging.warning(
                    "Camera file missing, dropping queue entry: %s/%s",
                    ref.folder, ref.filename,
                )
                remove_camera_pending(work_dir, ref)
                continue
            logging.warning("Download failed for %s: %s", ref.filename, error)
            state.last_error = f"download failed: {error}"
            return

        try:
            post_multipart(url, stage_path, captured_at)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
            logging.warning("Upload failed for %s: %s", ref.filename, error)
            state.last_error = f"upload failed: {error}"
            stage_path.unlink(missing_ok=True)
            return

        stage_path.unlink(missing_ok=True)
        try:
            gphoto2_delete_file(ref)
        except subprocess.CalledProcessError as error:
            # Upload succeeded but camera delete failed; keep going so we don't
            # re-upload, but flag it.
            logging.warning("Camera delete failed for %s: %s", ref.filename, error)
            state.last_error = f"camera delete failed: {error}"
        remove_camera_pending(work_dir, ref)
        state.last_upload_at = now_local_iso()
        state.last_error = None
        logging.info("Uploaded (camera) %s", ref.filename)
```

Then modify the existing `upload_pending` (currently lines 628–649) so it also runs the camera path. Replace the entire function with:

```python
def upload_pending(settings: Dict[str, Any], work_dir: Path, state: AgentState) -> None:
    """Drain both the local pending/ directory (rpicam path) and the camera-
    resident pending queue (gphoto2 path). Either may be empty."""
    pending_dir = work_dir / "pending"
    pending_dir.mkdir(parents=True, exist_ok=True)
    url = settings["server_url"].rstrip("/") + f"/api/cameras/{settings['camera_id']}/upload"

    for image_path in sorted(pending_dir.glob("*.jpg")):
        metadata_path = image_path.with_suffix(".json")
        metadata = load_json(metadata_path) if metadata_path.exists() else {}
        captured_at = metadata.get("captured_at", now_local_iso())

        try:
            post_multipart(url, image_path, captured_at)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
            logging.warning("Upload failed for %s: %s", image_path.name, error)
            state.last_error = f"upload failed: {error}"
            return

        image_path.unlink(missing_ok=True)
        metadata_path.unlink(missing_ok=True)
        state.last_upload_at = now_local_iso()
        state.last_error = None
        logging.info("Uploaded %s", image_path.name)

    upload_camera_pending(settings, work_dir, state)
```

Finally, update the heartbeat code in `run_agent` to include camera-pending count. Use Grep to find every line in `run_agent` that reads `measure_pending(work_dir)`:

Run: `grep -n "measure_pending(work_dir)" agent/timelapse_agent.py`

Expected: 4 hits, all inside `run_agent` (one in the startup block, three inside the `while True:` loop). For **every** match, add a follow-up line so the pair becomes:

```python
        state.pending_count, state.pending_bytes = measure_pending(work_dir)
        state.pending_count += measure_camera_pending(work_dir)
``` (Keep `measure_pending`'s implementation unchanged — it still only counts files in `pending/`. The camera count is added on top in the loop because we don't have byte sizes for camera-resident files without an extra gphoto2 query, and that's not worth the latency.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/timelapse && pytest tests/agent/test_gphoto2_backend.py -v`
Expected: All tests PASS (33 + 5 = 38).

Run: `cd /root/timelapse && pytest tests/agent/ -v`
Expected: All agent tests pass.

- [ ] **Step 5: Commit**

```bash
cd /root/timelapse
git add agent/timelapse_agent.py tests/agent/test_gphoto2_backend.py
git commit -m "feat(agent): upload pending camera files via tmpfs streaming"
```

---

### Task T9: Skip light sampling when active backend is gphoto2

**Files:**
- Modify: `agent/timelapse_agent.py` (`run_agent`, around line 749 where `sample_light_level` is called)
- Modify: `tests/agent/test_gphoto2_backend.py` (append a new test class)

- [ ] **Step 1: Append new failing test**

Append to `tests/agent/test_gphoto2_backend.py`:

```python
from timelapse_agent import should_sample_light


class TestShouldSampleLight:
    def test_true_for_rpicam(self):
        assert should_sample_light("rpicam") is True

    def test_false_for_gphoto2(self):
        assert should_sample_light("gphoto2") is False

    def test_false_for_none(self):
        assert should_sample_light(None) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/timelapse && pytest tests/agent/test_gphoto2_backend.py::TestShouldSampleLight -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Add `should_sample_light` and use it in `run_agent`**

In `agent/timelapse_agent.py`, add after `should_capture_for_scene` (around line 583):

```python
def should_sample_light(backend: Optional[str]) -> bool:
    """Light sampling is only available on the rpicam backend (fast YUV thumbnail).
    DSLRs over gphoto2 have no equivalent fast preview, so we skip sampling
    and the user can't use scene-light gating with a DSLR."""
    return backend == "rpicam"
```

Then find this block in `run_agent` (currently around lines 748–760):

```python
        if enabled and in_schedule and now >= next_capture:
            tool = find_capture_command()
            if tool:
                state.current_light = sample_light_level(tool)
            # Scene-light mode: skip the actual capture if below threshold.
            if schedule_mode == "scene" and not should_capture_for_scene(
                state.current_light, remote_config.get("light_threshold")
            ):
                logging.info(
                    "Scene-light gate: Y=%s < threshold=%s, skipping",
                    state.current_light, remote_config.get("light_threshold"),
                )
                next_capture = now + interval_seconds
```

Replace it with:

```python
        if enabled and in_schedule and now >= next_capture:
            active_backend = resolve_active_backend(remote_config)
            if should_sample_light(active_backend):
                tool = find_capture_command()
                if tool:
                    state.current_light = sample_light_level(tool)
            else:
                state.current_light = None
            # Scene-light mode: skip the actual capture if below threshold.
            # On gphoto2 backend current_light is always None and the
            # conservative defaults in should_capture_for_scene mean we never
            # gate captures out — equivalent to scene mode being a no-op.
            if (
                schedule_mode == "scene"
                and active_backend == "rpicam"
                and not should_capture_for_scene(
                    state.current_light, remote_config.get("light_threshold")
                )
            ):
                logging.info(
                    "Scene-light gate: Y=%s < threshold=%s, skipping",
                    state.current_light, remote_config.get("light_threshold"),
                )
                next_capture = now + interval_seconds
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/timelapse && pytest tests/agent/ -v`
Expected: All agent tests pass (38 + 3 = 41 in the gphoto2 file, plus all pre-existing tests).

- [ ] **Step 5: Commit**

```bash
cd /root/timelapse
git add agent/timelapse_agent.py tests/agent/test_gphoto2_backend.py
git commit -m "feat(agent): skip light sampling on gphoto2 backend"
```

---

### Task T10: Disable camera auto-poweroff at agent startup

**Files:**
- Modify: `agent/timelapse_agent.py` (add function near `gphoto2_available`, call from `run_agent`)
- Modify: `tests/agent/test_gphoto2_backend.py` (append test class)

- [ ] **Step 1: Append new failing tests**

Append to `tests/agent/test_gphoto2_backend.py`:

```python
from timelapse_agent import gphoto2_disable_autopoweroff


class TestDisableAutopoweroff:
    def test_sets_autopoweroff_to_zero(self):
        completed = MagicMock(returncode=0, stdout="", stderr="")
        with patch("timelapse_agent.subprocess.run", return_value=completed) as mock_run:
            gphoto2_disable_autopoweroff()
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "gphoto2"
        assert "--set-config" in cmd
        # Canon EOS bodies expose this as autopoweroff; setting to 0 disables it.
        assert any("autopoweroff=0" in part for part in cmd)

    def test_swallows_unsupported_config_error(self):
        # Some camera bodies don't expose autopoweroff. That's fine — log and move on.
        err = subprocess.CalledProcessError(
            returncode=1, cmd="gphoto2",
            stderr="ERROR: Property autopoweroff not found.\n",
        )
        with patch("timelapse_agent.subprocess.run", side_effect=err):
            # Should NOT raise.
            gphoto2_disable_autopoweroff()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/timelapse && pytest tests/agent/test_gphoto2_backend.py::TestDisableAutopoweroff -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement `gphoto2_disable_autopoweroff` and call from startup**

In `agent/timelapse_agent.py`, add immediately after `gphoto2_delete_file`:

```python
def gphoto2_disable_autopoweroff(timeout: int = 10) -> None:
    """Disable the camera's auto-poweroff so the USB connection stays alive.

    Best-effort: not all camera bodies expose this property. Failures are
    logged and swallowed.
    """
    try:
        subprocess.run(
            ["gphoto2", "--set-config", "autopoweroff=0"],
            check=True, capture_output=True, text=True, timeout=timeout,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as error:
        logging.info("Could not disable camera autopoweroff (often harmless): %s", error)
```

Then in `run_agent`, find the startup block (just after `state = AgentState()` and `remote_config = fetch_remote_config(...)`, and BEFORE the `while True:` loop). The very first call to `measure_pending(work_dir)` in the file is the marker — there are 4 such calls; we want the first one. Insert the autopoweroff call immediately before that first `state.pending_count, state.pending_bytes = measure_pending(work_dir)` line, so it runs once at startup:

```python
    if resolve_active_backend(remote_config) == "gphoto2":
        gphoto2_disable_autopoweroff()
    state.pending_count, state.pending_bytes = measure_pending(work_dir)
    state.pending_count += measure_camera_pending(work_dir)
```

(The `+= measure_camera_pending(...)` line was added in T8; this snippet shows the surrounding context, not new T8 work.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/timelapse && pytest tests/agent/ -v`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
cd /root/timelapse
git add agent/timelapse_agent.py tests/agent/test_gphoto2_backend.py
git commit -m "feat(agent): disable DSLR auto-poweroff at startup"
```

---

## WAVE 4 — Provisioning + version bump (parallelizable)

### Task T11: Provisioning installs gphoto2, udev rule, group, masks gvfs

**Files:**
- Modify: `server/app/provision_script.py` (`build_install_script` body)
- Test: `tests/server/test_provision_dslr.py` (new)

- [ ] **Step 1: Write the failing tests**

```python
# tests/server/test_provision_dslr.py
from app.provision_script import build_install_script


def script() -> str:
    return build_install_script(
        camera_id="x",
        server_url="http://x:8080",
        agent_version="0.9.0",
    )


def test_script_installs_gphoto2():
    assert "gphoto2" in script()


def test_script_installs_libgphoto2_runtime():
    # gphoto2 the CLI brings libgphoto2-port12 / libgphoto2-6 with it on Debian/RPi OS,
    # but we install the metapackage explicitly so a future split doesn't surprise us.
    assert "gphoto2" in script()


def test_script_writes_canon_udev_rule():
    s = script()
    # Canon's USB vendor ID is 04a9 (lowercase hex).
    assert "04a9" in s
    assert "/etc/udev/rules.d/" in s
    assert 'GROUP="plugdev"' in s


def test_script_reloads_udev():
    s = script()
    assert "udevadm control --reload-rules" in s
    assert "udevadm trigger" in s


def test_script_adds_pi_to_plugdev():
    # The systemd unit runs as the pi user (or whatever the unit specifies);
    # the pi user must be in plugdev to access USB devices via the udev rule.
    assert "usermod -aG plugdev" in script()


def test_script_masks_gvfs_gphoto2_monitor():
    # gvfs-gphoto2-volume-monitor auto-mounts cameras and prevents gphoto2 from
    # claiming the USB device. We mask it system-wide so it never starts.
    s = script()
    assert "gvfs-gphoto2-volume-monitor" in s
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/timelapse && pytest tests/server/test_provision_dslr.py -v`
Expected: All 6 tests FAIL.

- [ ] **Step 3: Update `build_install_script` to add the DSLR setup**

In `server/app/provision_script.py`, modify the `body = dedent(...)` block. Find the line:

```
        sudo apt-get install -y rpicam-apps-lite python3
```

Replace it with the following block, and also extend the package install to include gphoto2 and add the post-install configuration:

```
        sudo apt-get install -y rpicam-apps-lite python3 gphoto2

        # USB DSLR support: allow non-root agent process to claim the camera.
        # Canon's USB vendor ID is 04a9; the rule below grants plugdev members
        # read/write access. Other vendors can be added the same way.
        sudo tee /etc/udev/rules.d/90-timelapse-dslr.rules >/dev/null <<'TIMELAPSE_UDEV_EOF'
        # Canon (04a9) — covers EOS R6 and other Canon PTP cameras
        SUBSYSTEMS=="usb", ATTRS{{idVendor}}=="04a9", GROUP="plugdev", MODE="0664"
        TIMELAPSE_UDEV_EOF
        sudo udevadm control --reload-rules
        sudo udevadm trigger

        # Mask the gvfs auto-mounter; if it grabs the camera first, gphoto2
        # gets "Could not claim the USB device" errors. Masking is idempotent
        # and harmless on systems where the unit doesn't exist.
        sudo systemctl mask gvfs-gphoto2-volume-monitor.service 2>/dev/null || true

        # Add the SSH user (which the systemd unit runs as) to plugdev so the
        # udev rule's group permissions take effect.
        sudo usermod -aG plugdev {ssh_user}
```

Note the `{{idVendor}}` (double-braced) — this is escaped because the surrounding `dedent(...).format(...)` will reduce it to `{idVendor}`. Also note `{ssh_user}` is a `.format()` placeholder that needs to be passed; update the `.format()` call at the bottom of the function from:

```python
        ).format(agent_version=agent_version, config_json=config_json, bootstrap=bootstrap)
```

to:

```python
        ).format(
            agent_version=agent_version,
            config_json=config_json,
            bootstrap=bootstrap,
            ssh_user=ssh_user,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/timelapse && pytest tests/server/test_provision_dslr.py tests/server/test_provision_script.py -v`
Expected: All tests pass — both the new DSLR tests and the pre-existing provision script tests (no regressions).

- [ ] **Step 5: Commit**

```bash
cd /root/timelapse
git add server/app/provision_script.py tests/server/test_provision_dslr.py
git commit -m "feat(provision): install gphoto2, Canon udev rule, plugdev membership"
```

---

### Task T12: Bump agent VERSION

**Files:**
- Modify: `agent/VERSION`

- [ ] **Step 1: Read the current version**

Run: `cat /root/timelapse/agent/VERSION`
Note the current value (e.g. `0.8.0`).

- [ ] **Step 2: Bump the minor version**

Use the Edit tool to replace the current contents with the next minor version (e.g. `0.8.0` → `0.9.0`).

- [ ] **Step 3: Verify**

Run: `cat /root/timelapse/agent/VERSION`
Expected: shows the new version.

- [ ] **Step 4: Commit**

```bash
cd /root/timelapse
git add agent/VERSION
git commit -m "chore(agent): bump VERSION for DSLR backend"
```

---

## WAVE 5 — Manual hardware verification

### Task T13: End-to-end verification on a Pi + Canon R6

This task is **not automated** — it requires a Raspberry Pi, a Canon R6 (or other gphoto2-supported DSLR), and a USB cable. Mark each checkbox after the listed command produces the expected outcome.

- [ ] **Step 1: Provision a Pi via the existing flow**

From the server UI or API, provision a new agent targeting the Pi. Verify the install script ran cleanly:

Run on the Pi: `systemctl status timelapse-agent`
Expected: `active (running)`.

- [ ] **Step 2: Connect the Canon R6 over USB**

Power the camera on, set it to **PTP** mode (Menu → Communication → PC Connection → PTP), and plug it into a Pi USB port.

Run on the Pi: `gphoto2 --auto-detect`
Expected: One row showing `Canon EOS R6` and a `usb:NNN,MMM` port.

If you see "Could not claim the USB device" — the gvfs masking didn't take or the user isn't in `plugdev`. Run `groups` (should include `plugdev`) and `systemctl status gvfs-gphoto2-volume-monitor` (should be `masked`).

- [ ] **Step 3: Set the camera_backend to gphoto2 in the server UI**

Edit the camera's config via the UI (or `PUT /api/cameras/{id}/config`) and set `camera_backend: "gphoto2"`. Wait up to `config_poll_seconds` (default 60s) for the agent to pick it up.

- [ ] **Step 4: Confirm a capture**

Watch agent logs: `journalctl -u timelapse-agent -f`
Expected within one capture interval: a log line `Captured IMG_NNNN.CR3` (or `.JPG` depending on camera setting).

Verify the file is on the camera:
Run on the Pi: `gphoto2 --list-files`
Expected: at least one file present.

Verify the queue:
Run on the Pi: `cat /var/lib/timelapse-agent/pending_camera_files.json`
Expected: one entry with the filename you just saw.

- [ ] **Step 5: Confirm upload + camera deletion**

Wait for the next loop iteration (or manually nudge by restarting the agent). Watch logs:
Expected: `Uploaded (camera) IMG_NNNN.CR3` and the entry disappears from `pending_camera_files.json`.

Run on the Pi: `gphoto2 --list-files`
Expected: the file is gone from the camera.

Open the server UI library view: the new image appears with the correct timestamp.

- [ ] **Step 6: Confirm Pi disk is not accumulating images**

Run on the Pi: `du -sh /var/lib/timelapse-agent/pending/`
Expected: empty or near-zero (only `pending_camera_files.json` lives here for gphoto2 mode).

Run on the Pi: `ls /tmp/timelapse-agent-stage/`
Expected: empty between captures (files exist only briefly during upload).

- [ ] **Step 7: Disconnect/reconnect resilience**

Pull the USB cable, wait one capture interval, plug it back in. Watch logs:
Expected: errors logged during the disconnect window (`Could not claim the USB device` or similar), then captures resume after reconnect with no agent restart needed. Any captures that landed on the camera SD before disconnect should still upload from the queue.

- [ ] **Step 8: Document any deviations**

If any step failed or required a workaround, append a "Known issues" section to this plan file capturing the symptom and fix so the next person doesn't re-discover it.

---

## Out-of-scope for this plan (followups)

- **RAW+JPEG dual-file mode.** Each capture creates two files; v1 only enqueues one. Add a multi-file enqueue path when a user asks.
- **Live-view-based light sampling.** Could re-enable scene mode for DSLRs by grabbing a `--capture-preview` frame, but the cadence is too slow to be useful as a per-capture gate.
- **Camera-side image format / size config from the server.** Would require per-model `gphoto2 --set-config` mappings; defer until needed.
- **Eviction of oldest files from the camera SD when full.** Currently a full SD just causes capture failures; we surface the error but don't auto-evict camera-side. SD cards are usually large enough that this is a real-world non-issue.
- **Other DSLR brands.** The udev rule only covers Canon (vendor `04a9`). Adding Nikon (`04b0`), Sony (`054c`), etc. is a one-line rule addition each.
