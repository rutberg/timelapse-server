# RAM-First Pending Queue with SD Spill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Captures land in a tmpfs (RAM) directory first; failed uploads are spilled to an SD-card directory so they survive reboots without ever wearing the SD card during normal operation.

**Architecture:** A new `resolve_pending_dirs(settings, work_dir)` helper returns two paths — `ram_dir` (tmpfs, fast) and `spill_dir` (SD, durable). Captures always write to `ram_dir`. The upload loop drains `spill_dir` before `ram_dir` (FIFO across tiers). On upload failure the failed file moves from `ram_dir` to `spill_dir`. If `ram_pending_dir` is absent from config both paths collapse to `work_dir/pending/` and the existing behaviour is preserved exactly. The systemd service uses `RuntimeDirectory=` to provide a tmpfs `/run/timelapse-agent/` directory automatically.

**Tech Stack:** Python 3.11 stdlib (`pathlib`, `shutil`), pytest, systemd `RuntimeDirectory`

---

## File Map

| File | Change |
|---|---|
| `agent/timelapse_agent.py` | Add `resolve_pending_dirs`; update `capture_frame`, `upload_pending`, `evict_pending`, `measure_pending`, `run_agent` |
| `agent/systemd/timelapse-agent.service` | Add `RuntimeDirectory=timelapse-agent RuntimeDirectorySize=256M` |
| `server/app/provision_script.py` | Add `"ram_pending_dir": "/run/timelapse-agent/pending"` to generated config |
| `tests/agent/test_pending_spill.py` | New — all tests for the two-tier behaviour |

---

## Task 1: `resolve_pending_dirs` helper

**Files:**
- Modify: `agent/timelapse_agent.py` (add after `resolve_max_pending_bytes`, ~line 112)
- Test: `tests/agent/test_pending_spill.py` (new file)

- [ ] **Step 1: Create the test file with failing tests**

```python
# tests/agent/test_pending_spill.py
from pathlib import Path
import timelapse_agent as agent


def test_resolve_pending_dirs_no_config_returns_single_tier(tmp_path):
    settings = {}
    ram_dir, spill_dir = agent.resolve_pending_dirs(settings, tmp_path)
    assert ram_dir == tmp_path / "pending"
    assert spill_dir == tmp_path / "pending"


def test_resolve_pending_dirs_configured_returns_two_tiers(tmp_path):
    ram_path = tmp_path / "ram" / "pending"
    settings = {"ram_pending_dir": str(ram_path)}
    ram_dir, spill_dir = agent.resolve_pending_dirs(settings, tmp_path)
    assert ram_dir == ram_path
    assert spill_dir == tmp_path / "spill"


def test_resolve_pending_dirs_two_tiers_are_distinct(tmp_path):
    settings = {"ram_pending_dir": "/run/timelapse-agent/pending"}
    ram_dir, spill_dir = agent.resolve_pending_dirs(settings, tmp_path)
    assert ram_dir != spill_dir
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd /root/timelapse && python3 -m pytest tests/agent/test_pending_spill.py -v
```
Expected: `AttributeError: module 'timelapse_agent' has no attribute 'resolve_pending_dirs'`

- [ ] **Step 3: Implement `resolve_pending_dirs` in `timelapse_agent.py`**

Add immediately after the `resolve_max_pending_bytes` function (~line 112):

```python
def resolve_pending_dirs(
    settings: Dict[str, Any], work_dir: Path
) -> tuple[Path, Path]:
    """Return (ram_dir, spill_dir).

    If 'ram_pending_dir' is set in settings, captures write to that path
    (typically a tmpfs) and failures spill to work_dir/spill/.
    If absent, both paths are work_dir/pending/ — legacy single-tier behaviour,
    no spill directory is used.
    """
    configured = settings.get("ram_pending_dir")
    if configured:
        return Path(configured), work_dir / "spill"
    pending = work_dir / "pending"
    return pending, pending
```

- [ ] **Step 4: Run tests to confirm pass**

```bash
cd /root/timelapse && python3 -m pytest tests/agent/test_pending_spill.py -v
```
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add agent/timelapse_agent.py tests/agent/test_pending_spill.py
git commit -m "feat(agent): add resolve_pending_dirs for RAM/spill two-tier config"
```

---

## Task 2: `measure_pending` across both tiers

**Files:**
- Modify: `agent/timelapse_agent.py` — `measure_pending` signature and body (~line 222)
- Test: `tests/agent/test_pending_spill.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/agent/test_pending_spill.py`:

```python
def test_measure_pending_single_tier(tmp_path):
    pending = tmp_path / "pending"
    pending.mkdir()
    (pending / "20260101T000000.jpg").write_bytes(b"x" * 1000)
    count, total = agent.measure_pending(tmp_path / "pending", tmp_path / "pending")
    assert count == 1
    assert total == 1000


def test_measure_pending_two_tiers(tmp_path):
    ram = tmp_path / "ram"
    spill = tmp_path / "spill"
    ram.mkdir(); spill.mkdir()
    (ram / "20260101T000001.jpg").write_bytes(b"x" * 500)
    (spill / "20260101T000000.jpg").write_bytes(b"x" * 800)
    count, total = agent.measure_pending(ram, spill)
    assert count == 2
    assert total == 1300


def test_measure_pending_missing_dirs(tmp_path):
    count, total = agent.measure_pending(tmp_path / "ram", tmp_path / "spill")
    assert count == 0
    assert total == 0
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd /root/timelapse && python3 -m pytest tests/agent/test_pending_spill.py::test_measure_pending_single_tier -v
```
Expected: FAIL (wrong signature)

- [ ] **Step 3: Update `measure_pending` to accept explicit dirs**

Replace the existing `measure_pending` function (~line 222):

```python
def measure_pending(ram_dir: Path, spill_dir: Path) -> tuple[int, int]:
    """Count and size all queued JPEGs across both pending tiers."""
    count = 0
    total = 0
    dirs = {ram_dir, spill_dir}  # set deduplicates when single-tier
    for d in dirs:
        if not d.exists():
            continue
        for entry in d.glob("*.jpg"):
            try:
                total += entry.stat().st_size
            except OSError:
                continue
            count += 1
    return (count, total)
```

- [ ] **Step 4: Fix the one internal caller in `run_agent`**

Find this block (~line 1623) and update the call:

```python
# OLD
state.pending_count, state.pending_bytes = measure_pending(work_dir)

# NEW  — ram_dir and spill_dir will be wired in Task 7; for now use the same
# temporary shim so existing code still compiles
state.pending_count, state.pending_bytes = measure_pending(
    work_dir / "pending", work_dir / "pending"
)
```

- [ ] **Step 5: Run all agent tests**

```bash
cd /root/timelapse && python3 -m pytest tests/agent/ -v
```
Expected: 126 + 3 new = 129 passed

- [ ] **Step 6: Commit**

```bash
git add agent/timelapse_agent.py tests/agent/test_pending_spill.py
git commit -m "feat(agent): measure_pending accepts explicit ram_dir and spill_dir"
```

---

## Task 3: `evict_pending` targets spill tier only

**Files:**
- Modify: `agent/timelapse_agent.py` — `evict_pending` signature and body (~line 237)
- Test: `tests/agent/test_pending_spill.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/agent/test_pending_spill.py`:

```python
def test_evict_pending_removes_oldest_from_spill(tmp_path):
    spill = tmp_path / "spill"
    spill.mkdir()
    (spill / "20260101T000000.jpg").write_bytes(b"a" * 600)
    (spill / "20260101T000001.jpg").write_bytes(b"b" * 600)
    evicted_count, evicted_bytes = agent.evict_pending(spill, max_bytes=700)
    assert evicted_count == 1
    assert evicted_bytes == 600
    remaining = list(spill.glob("*.jpg"))
    assert len(remaining) == 1
    assert remaining[0].name == "20260101T000001.jpg"


def test_evict_pending_removes_sidecar(tmp_path):
    spill = tmp_path / "spill"
    spill.mkdir()
    (spill / "20260101T000000.jpg").write_bytes(b"a" * 1000)
    (spill / "20260101T000000.json").write_text('{"captured_at":"2026-01-01T00:00:00+00:00"}')
    agent.evict_pending(spill, max_bytes=0)
    # max_bytes=0 means unlimited — nothing evicted
    assert (spill / "20260101T000000.jpg").exists()


def test_evict_pending_zero_means_unlimited(tmp_path):
    spill = tmp_path / "spill"
    spill.mkdir()
    (spill / "20260101T000000.jpg").write_bytes(b"x" * 1000)
    count, _ = agent.evict_pending(spill, max_bytes=0)
    assert count == 0


def test_evict_pending_missing_dir_is_noop(tmp_path):
    count, _ = agent.evict_pending(tmp_path / "spill", max_bytes=100)
    assert count == 0
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd /root/timelapse && python3 -m pytest tests/agent/test_pending_spill.py::test_evict_pending_removes_oldest_from_spill -v
```
Expected: FAIL (wrong signature — `evict_pending` currently takes `work_dir`)

- [ ] **Step 3: Update `evict_pending` to accept explicit `spill_dir`**

Replace the existing `evict_pending` function (~line 237):

```python
def evict_pending(spill_dir: Path, max_bytes: int) -> tuple[int, int]:
    """Delete oldest spill-tier captures until total size <= max_bytes.

    Only the SD spill directory is evicted; the RAM pending directory is
    never touched here — images there either upload successfully (and are
    deleted) or are moved to spill on failure.
    max_bytes <= 0 means no cap (returns 0, 0). Sidecar .json metadata is
    removed alongside its image.
    """
    if max_bytes <= 0:
        return (0, 0)
    if not spill_dir.exists():
        return (0, 0)
    files = sorted(spill_dir.glob("*.jpg"))
    sizes = []
    total = 0
    for path in files:
        try:
            size = path.stat().st_size
        except OSError:
            sizes.append(0)
            continue
        sizes.append(size)
        total += size
    if total <= max_bytes:
        return (0, 0)
    evicted_count = 0
    evicted_bytes = 0
    for path, size in zip(files, sizes):
        if total <= max_bytes:
            break
        try:
            path.unlink()
        except OSError:
            continue
        path.with_suffix(".json").unlink(missing_ok=True)
        total -= size
        evicted_count += 1
        evicted_bytes += size
    return (evicted_count, evicted_bytes)
```

- [ ] **Step 4: Fix the internal caller in `run_agent` (temporary shim)**

Find (~line 1616):
```python
evicted_count, evicted_bytes = evict_pending(work_dir, max_pending_bytes)
```
Change to:
```python
evicted_count, evicted_bytes = evict_pending(work_dir / "spill", max_pending_bytes)
```
This shim is replaced with the proper `spill_dir` variable in Task 7.

- [ ] **Step 5: Run all agent tests**

```bash
cd /root/timelapse && python3 -m pytest tests/agent/ -v
```
Expected: all previous + 4 new = 133 passed

- [ ] **Step 6: Commit**

```bash
git add agent/timelapse_agent.py tests/agent/test_pending_spill.py
git commit -m "feat(agent): evict_pending targets spill_dir only, never RAM pending"
```

---

## Task 4: `upload_pending` — drain spill first, spill on failure

**Files:**
- Modify: `agent/timelapse_agent.py` — `upload_pending` (~line 1411)
- Test: `tests/agent/test_pending_spill.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/agent/test_pending_spill.py`:

```python
import json as _json
from unittest.mock import patch, MagicMock
from urllib.error import URLError


def _fake_settings():
    return {"camera_id": "cam1", "server_url": "http://server.local:8081"}


def _make_jpg(directory: Path, name: str, size: int = 100) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / name
    p.write_bytes(b"J" * size)
    p.with_suffix(".json").write_text(
        _json.dumps({"captured_at": "2026-01-01T00:00:00+00:00"})
    )
    return p


def test_upload_pending_drains_spill_before_ram(tmp_path):
    """Spill files must be uploaded before RAM files (chronological order)."""
    spill = tmp_path / "spill"
    ram = tmp_path / "ram"
    _make_jpg(spill, "20260101T000000.jpg")  # older
    _make_jpg(ram,   "20260101T000001.jpg")  # newer
    uploaded = []

    def fake_post(url, path, captured_at, **kw):
        uploaded.append(path.parent.name)  # "spill" or "ram"

    state = agent.AgentState()
    with patch.object(agent, "post_multipart", side_effect=fake_post):
        agent.upload_pending(_fake_settings(), ram, spill, state)

    assert uploaded == ["spill", "ram"]
    assert not (spill / "20260101T000000.jpg").exists()
    assert not (ram   / "20260101T000001.jpg").exists()


def test_upload_pending_moves_ram_failure_to_spill(tmp_path):
    """On upload failure from RAM, the file moves to spill (not deleted)."""
    spill = tmp_path / "spill"
    ram = tmp_path / "ram"
    jpg = _make_jpg(ram, "20260101T000002.jpg")

    def fake_post(url, path, captured_at, **kw):
        raise URLError("connection refused")

    state = agent.AgentState()
    with patch.object(agent, "post_multipart", side_effect=fake_post):
        agent.upload_pending(_fake_settings(), ram, spill, state)

    assert not jpg.exists()                          # gone from RAM
    assert (spill / "20260101T000002.jpg").exists()  # landed in spill
    assert (spill / "20260101T000002.json").exists() # sidecar moved too
    assert "upload failed" in state.last_error


def test_upload_pending_single_tier_failure_leaves_file(tmp_path):
    """In single-tier mode (ram==spill), failure leaves file in place (legacy)."""
    pending = tmp_path / "pending"
    jpg = _make_jpg(pending, "20260101T000003.jpg")

    def fake_post(url, path, captured_at, **kw):
        raise URLError("connection refused")

    state = agent.AgentState()
    with patch.object(agent, "post_multipart", side_effect=fake_post):
        agent.upload_pending(_fake_settings(), pending, pending, state)

    assert jpg.exists()  # file stays in place in single-tier mode


def test_upload_pending_single_tier_success_deletes_file(tmp_path):
    pending = tmp_path / "pending"
    jpg = _make_jpg(pending, "20260101T000004.jpg")

    state = agent.AgentState()
    with patch.object(agent, "post_multipart", return_value=None):
        with patch.object(agent, "upload_camera_pending", return_value=None):
            agent.upload_pending(_fake_settings(), pending, pending, state)

    assert not jpg.exists()
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd /root/timelapse && python3 -m pytest tests/agent/test_pending_spill.py::test_upload_pending_drains_spill_before_ram -v
```
Expected: FAIL (wrong signature)

- [ ] **Step 3: Update `upload_pending` signature and body**

Replace `upload_pending` (~line 1411):

```python
def upload_pending(
    settings: Dict[str, Any],
    ram_dir: Path,
    spill_dir: Path,
    state: AgentState,
) -> None:
    """Drain both pending tiers and the camera-resident queue.

    Order: spill (SD, older) → ram (RAM, newer) → camera queue.
    On upload failure from the RAM tier the file is moved to spill so it
    survives a restart. In single-tier mode (ram_dir == spill_dir) a
    failure is left in place exactly as before.
    """
    url = (
        settings["server_url"].rstrip("/")
        + f"/api/cameras/{settings['camera_id']}/upload"
    )

    def _upload_dir(directory: Path, on_failure: str) -> bool:
        """Upload all JPEGs in directory. Return False and stop on first failure.

        on_failure: 'spill' moves the failed file to spill_dir;
                    'leave' leaves it in place (single-tier / spill tier itself).
        """
        if not directory.exists():
            return True
        for image_path in sorted(directory.glob("*.jpg")):
            metadata_path = image_path.with_suffix(".json")
            metadata = load_json(metadata_path) if metadata_path.exists() else {}
            captured_at = metadata.get("captured_at", now_local_iso())
            try:
                post_multipart(url, image_path, captured_at)
            except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
                logging.warning("Upload failed for %s: %s", image_path.name, error)
                state.last_error = f"upload failed: {error}"
                if on_failure == "spill" and directory != spill_dir:
                    spill_dir.mkdir(parents=True, exist_ok=True)
                    try:
                        image_path.replace(spill_dir / image_path.name)
                        if metadata_path.exists():
                            metadata_path.replace(spill_dir / metadata_path.name)
                    except OSError as move_err:
                        logging.warning(
                            "Could not spill %s to SD: %s", image_path.name, move_err
                        )
                return False
            image_path.unlink(missing_ok=True)
            metadata_path.unlink(missing_ok=True)
            state.last_upload_at = now_local_iso()
            state.last_error = None
            logging.info("Uploaded %s", image_path.name)
        return True

    spill_dir.mkdir(parents=True, exist_ok=True)
    ram_dir.mkdir(parents=True, exist_ok=True)

    if not _upload_dir(spill_dir, on_failure="leave"):
        return
    _upload_dir(ram_dir, on_failure="spill")
    upload_camera_pending(settings, ram_dir.parent if ram_dir != spill_dir else ram_dir.parent, state)
```

Wait — `upload_camera_pending` takes `work_dir`, not `ram_dir`. We need to keep passing `work_dir`. Update the body to pass `work_dir` to `upload_camera_pending` via the `run_agent` caller instead. For now, the simplest fix: store a reference to `work_dir` alongside the dirs in Task 7. The function signature becomes:

```python
def upload_pending(
    settings: Dict[str, Any],
    work_dir: Path,
    ram_dir: Path,
    spill_dir: Path,
    state: AgentState,
) -> None:
    url = (
        settings["server_url"].rstrip("/")
        + f"/api/cameras/{settings['camera_id']}/upload"
    )

    def _upload_dir(directory: Path, on_failure: str) -> bool:
        if not directory.exists():
            return True
        for image_path in sorted(directory.glob("*.jpg")):
            metadata_path = image_path.with_suffix(".json")
            metadata = load_json(metadata_path) if metadata_path.exists() else {}
            captured_at = metadata.get("captured_at", now_local_iso())
            try:
                post_multipart(url, image_path, captured_at)
            except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
                logging.warning("Upload failed for %s: %s", image_path.name, error)
                state.last_error = f"upload failed: {error}"
                if on_failure == "spill" and directory != spill_dir:
                    spill_dir.mkdir(parents=True, exist_ok=True)
                    try:
                        image_path.replace(spill_dir / image_path.name)
                        if metadata_path.exists():
                            metadata_path.replace(spill_dir / metadata_path.name)
                    except OSError as move_err:
                        logging.warning(
                            "Could not spill %s to SD: %s", image_path.name, move_err
                        )
                return False
            image_path.unlink(missing_ok=True)
            metadata_path.unlink(missing_ok=True)
            state.last_upload_at = now_local_iso()
            state.last_error = None
            logging.info("Uploaded %s", image_path.name)
        return True

    spill_dir.mkdir(parents=True, exist_ok=True)
    ram_dir.mkdir(parents=True, exist_ok=True)

    if not _upload_dir(spill_dir, on_failure="leave"):
        return
    _upload_dir(ram_dir, on_failure="spill")
    upload_camera_pending(settings, work_dir, state)
```

Update the test helpers to pass `work_dir` (add a `tmp_path` as `work_dir`):

```python
# In tests, pass ram_dir.parent as work_dir (or tmp_path directly)
agent.upload_pending(_fake_settings(), tmp_path, ram, spill, state)
agent.upload_pending(_fake_settings(), tmp_path, pending, pending, state)
```

Update each test call to add `tmp_path` as the second argument.

- [ ] **Step 4: Update test calls to pass `work_dir`**

In `tests/agent/test_pending_spill.py`, update every `upload_pending` call to add `tmp_path` as second argument:

```python
# test_upload_pending_drains_spill_before_ram
agent.upload_pending(_fake_settings(), tmp_path, ram, spill, state)

# test_upload_pending_moves_ram_failure_to_spill
agent.upload_pending(_fake_settings(), tmp_path, ram, spill, state)

# test_upload_pending_single_tier_failure_leaves_file
agent.upload_pending(_fake_settings(), tmp_path, pending, pending, state)

# test_upload_pending_single_tier_success_deletes_file
agent.upload_pending(_fake_settings(), tmp_path, pending, pending, state)
```

- [ ] **Step 5: Fix the caller in `run_agent` (temporary shim)**

Find (~line 1622):
```python
upload_pending(settings, work_dir, state)
```
Change to:
```python
upload_pending(settings, work_dir, work_dir / "pending", work_dir / "spill", state)
```
This shim is replaced in Task 7.

- [ ] **Step 6: Run all agent tests**

```bash
cd /root/timelapse && python3 -m pytest tests/agent/ -v
```
Expected: all previous + 4 new upload tests = 137 passed

- [ ] **Step 7: Commit**

```bash
git add agent/timelapse_agent.py tests/agent/test_pending_spill.py
git commit -m "feat(agent): upload_pending drains spill-first, moves RAM failures to spill"
```

---

## Task 5: `capture_frame` writes to `ram_dir`

**Files:**
- Modify: `agent/timelapse_agent.py` — `capture_frame` signature (~line 1308)
- Test: `tests/agent/test_pending_spill.py`

- [ ] **Step 1: Add failing test**

Append to `tests/agent/test_pending_spill.py`:

```python
def test_capture_frame_writes_to_ram_dir(tmp_path):
    """capture_frame must write the JPEG to the configured ram_dir, not work_dir/pending."""
    ram_dir = tmp_path / "run" / "pending"
    config = {"camera_backend": "rpicam"}

    fake_jpg = b"\xff\xd8\xff" + b"\x00" * 100

    with patch.object(agent, "resolve_active_backend", return_value="rpicam"), \
         patch.object(agent, "find_capture_command", return_value="/usr/bin/rpicam-still"), \
         patch.object(agent, "build_capture_command", return_value=["rpicam-still"]), \
         patch("timelapse_agent.subprocess.run") as mock_run:

        def fake_run(cmd, **kw):
            # Write a fake JPEG to whatever --output path was given
            for i, arg in enumerate(cmd):
                if arg in ("-o", "--output") and i + 1 < len(cmd):
                    Path(cmd[i + 1]).write_bytes(fake_jpg)
                    break
            result = MagicMock()
            result.returncode = 0
            return result

        mock_run.side_effect = fake_run
        result = agent.capture_frame(ram_dir, config)

    assert result.parent == ram_dir
    assert result.suffix == ".jpg"
    assert result.exists()
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd /root/timelapse && python3 -m pytest tests/agent/test_pending_spill.py::test_capture_frame_writes_to_ram_dir -v
```
Expected: FAIL (currently `capture_frame` ignores the first arg's name and always writes to `work_dir/pending`)

- [ ] **Step 3: Update `capture_frame` to accept `ram_dir` instead of `work_dir`**

The current signature is `capture_frame(work_dir: Path, config)`. Change it so the first argument is the capture destination directly:

```python
def capture_frame(ram_dir: Path, config: Dict[str, Any]) -> Path:
    """Trigger a capture using the configured backend.

    Always returns a Path to a JPEG written under ram_dir.
    ram_dir is typically a tmpfs directory; callers are responsible for
    creating it before calling this function.
    """
    backend = resolve_active_backend(config)
    if backend is None:
        raise RuntimeError(
            "No camera backend available: install rpicam-apps-lite for Pi cameras "
            "or gphoto2 + a USB DSLR"
        )

    captured_at_filename = datetime.now().strftime("%Y%m%dT%H%M%S")
    output_path = ram_dir / f"{captured_at_filename}.jpg"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_suffix(".tmp.jpg")

    if backend == "gphoto2":
        # ... (rest of existing gphoto2 block, unchanged — just uses ram_dir now)
```

The body of the function is otherwise unchanged — just replace every `work_dir / "pending"` reference with `ram_dir` (there are none in the gphoto2/rpicam paths since they already used `output_path.parent`).

Full replacement (keep gphoto2 and rpicam blocks identical to current, only the function signature and first lines change):

```python
def capture_frame(ram_dir: Path, config: Dict[str, Any]) -> Path:
    """Trigger a capture using the configured backend.

    Writes the captured JPEG into ram_dir (which is typically a tmpfs
    directory; the caller creates it). Returns the Path to the new file.
    """
    backend = resolve_active_backend(config)
    if backend is None:
        raise RuntimeError(
            "No camera backend available: install rpicam-apps-lite for Pi cameras "
            "or gphoto2 + a USB DSLR"
        )

    captured_at_filename = datetime.now().strftime("%Y%m%dT%H%M%S")
    output_path = ram_dir / f"{captured_at_filename}.jpg"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_suffix(".tmp.jpg")

    if backend == "gphoto2":
        result = subprocess.run(
            [
                "gphoto2",
                "--capture-image-and-download",
                "--filename", str(temp_path),
                "--force-overwrite",
            ],
            check=True, capture_output=True, text=True, timeout=60,
            cwd=str(output_path.parent),
        )
        if not temp_path.exists():
            actual = _gphoto2_actual_download(result.stdout, output_path.parent)
            if actual is None or not actual.exists():
                raise RuntimeError(
                    f"gphoto2 capture-and-download succeeded but no file found "
                    f"at {temp_path} (stdout: {result.stdout.strip()!r})"
                )
            actual.replace(temp_path)
        temp_path.replace(output_path)
        metadata = {"captured_at": now_local_iso(), "hostname": socket.gethostname()}
        write_json(output_path.with_suffix(".json"), metadata)
        return output_path

    # rpicam path
    command = find_capture_command()
    captured_at_filename = datetime.now().strftime("%Y%m%dT%H%M%S")
    output_path = ram_dir / f"{captured_at_filename}.jpg"
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

- [ ] **Step 4: Fix the caller in `run_agent` (temporary shim)**

Find (~line 1606):
```python
image_path = capture_frame(work_dir, remote_config)
```
Change to:
```python
image_path = capture_frame(work_dir / "pending", remote_config)
```
This shim is replaced in Task 7.

- [ ] **Step 5: Run all agent tests**

```bash
cd /root/timelapse && python3 -m pytest tests/agent/ -v
```
Expected: all previous + 1 new = 138 passed

- [ ] **Step 6: Commit**

```bash
git add agent/timelapse_agent.py tests/agent/test_pending_spill.py
git commit -m "feat(agent): capture_frame writes to explicit ram_dir argument"
```

---

## Task 6: Wire `run_agent` — resolve dirs, pass through, remove shims

**Files:**
- Modify: `agent/timelapse_agent.py` — `run_agent` function (~line 1445)

- [ ] **Step 1: Update `run_agent` to resolve dirs and thread them through**

In `run_agent`, after `work_dir.mkdir(...)` add:

```python
ram_dir, spill_dir = resolve_pending_dirs(settings, work_dir)
ram_dir.mkdir(parents=True, exist_ok=True)
if spill_dir != ram_dir:
    spill_dir.mkdir(parents=True, exist_ok=True)
```

Update the log line to include the tier paths:
```python
logging.info(
    "Agent v%s started for camera_id=%s (ram_dir=%s spill_dir=%s max_pending_bytes=%s)",
    AGENT_VERSION, settings["camera_id"], ram_dir, spill_dir, max_pending_bytes,
)
```

Replace the three shim calls with the real arguments:

```python
# capture_frame call (~line 1606)
image_path = capture_frame(ram_dir, remote_config)

# evict_pending call (~line 1616)
evicted_count, evicted_bytes = evict_pending(spill_dir, max_pending_bytes)

# upload_pending call (~line 1622)
upload_pending(settings, work_dir, ram_dir, spill_dir, state)

# measure_pending call (~line 1623)
state.pending_count, state.pending_bytes = measure_pending(ram_dir, spill_dir)
```

- [ ] **Step 2: Run all agent tests**

```bash
cd /root/timelapse && python3 -m pytest tests/agent/ -v
```
Expected: 138 passed, 0 failed

- [ ] **Step 3: Commit**

```bash
git add agent/timelapse_agent.py
git commit -m "feat(agent): wire run_agent with resolve_pending_dirs, remove shims"
```

---

## Task 7: Systemd service — `RuntimeDirectory` for tmpfs

**Files:**
- Modify: `agent/systemd/timelapse-agent.service`

- [ ] **Step 1: Update the service file**

Replace the `[Service]` section with:

```ini
[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/timelapse-agent/current/timelapse_agent.py --config /etc/timelapse-agent/config.json
Restart=always
RestartSec=10
RuntimeDirectory=timelapse-agent
RuntimeDirectorySize=256M
```

`RuntimeDirectory=timelapse-agent` causes systemd to create `/run/timelapse-agent/` as a tmpfs directory owned by the service's user on each start, and remove it on stop. `RuntimeDirectorySize=256M` caps RAM use; adjust in provisioned config if needed. At 1 frame/minute and ~3 MB/frame a 256 MB tmpfs holds ~85 frames — more than enough buffer for a connectivity blip.

- [ ] **Step 2: Verify the service file parses**

```bash
systemd-analyze verify /root/timelapse/agent/systemd/timelapse-agent.service 2>&1 || true
```
Expected: no output (warnings about missing unit dependencies are acceptable; errors are not)

- [ ] **Step 3: Commit**

```bash
git add agent/systemd/timelapse-agent.service
git commit -m "feat(agent): add RuntimeDirectory=timelapse-agent for tmpfs pending queue"
```

---

## Task 8: Provision script — include `ram_pending_dir` in generated config

**Files:**
- Modify: `server/app/provision_script.py` (~line 35)
- Test: existing `tests/server/` — add one assertion to the provision script tests

- [ ] **Step 1: Find the provision script test**

```bash
grep -n "config_json\|ram_pending\|work_dir" /root/timelapse/tests/server/test_provision_script.py | head -20
```

- [ ] **Step 2: Add a failing assertion**

Open `tests/server/test_provision_script.py` and find the test that inspects the generated config JSON. Add:

```python
assert '"ram_pending_dir": "/run/timelapse-agent/pending"' in script
```

Run to confirm it fails:
```bash
cd /root/timelapse && python3 -m pytest tests/server/test_provision_script.py -v
```

- [ ] **Step 3: Update `build_install_script` config template**

In `server/app/provision_script.py`, update the `config_json` dict (~line 35):

```python
config_json = json.dumps(
    {
        "camera_id": camera_id,
        "server_url": server_url,
        "config_poll_seconds": 60,
        "work_dir": "/var/lib/timelapse-agent",
        "ram_pending_dir": "/run/timelapse-agent/pending",
    },
    indent=2,
)
```

- [ ] **Step 4: Run all tests**

```bash
cd /root/timelapse && python3 -m pytest tests/ -v
```
Expected: all previous + 1 new = all passed

- [ ] **Step 5: Commit**

```bash
git add server/app/provision_script.py tests/server/test_provision_script.py
git commit -m "feat(agent): provision config includes ram_pending_dir for tmpfs queue"
```

---

## Self-Review

**Spec coverage:**
- ✅ RAM-first captures (`capture_frame` writes to `ram_dir`)
- ✅ Spill to SD on failure (`upload_pending` moves to `spill_dir`)
- ✅ Drain spill before RAM (`_upload_dir(spill_dir)` called first)
- ✅ Survive reboots (spill is on SD, persists across service restarts)
- ✅ Legacy single-tier backward compat (`ram_dir == spill_dir` when unconfigured)
- ✅ No SD wear in normal operation (captures → RAM → upload → deleted)
- ✅ Eviction targets SD spill only (RAM is never evicted, images move to spill instead)
- ✅ systemd provides tmpfs automatically (`RuntimeDirectory`)
- ✅ Provision script sets `ram_pending_dir` in generated config

**Placeholder scan:** None found.

**Type consistency:**
- `resolve_pending_dirs` returns `tuple[Path, Path]` — matches all callsites
- `measure_pending(ram_dir, spill_dir)` — consistent across Task 2 and Task 6
- `evict_pending(spill_dir, max_bytes)` — consistent across Task 3 and Task 6
- `capture_frame(ram_dir, config)` — consistent across Task 5 and Task 6
- `upload_pending(settings, work_dir, ram_dir, spill_dir, state)` — consistent across Task 4 and Task 6
