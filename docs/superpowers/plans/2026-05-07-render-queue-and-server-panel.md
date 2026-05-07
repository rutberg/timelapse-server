# Render Queue & Server Info Panel — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace synchronous render-in-request with a global single-job queue (`RenderRunner`), expand the sidebar bottom into a server info panel that shows storage + live render activity + queue with per-row abort, and wire up the dead Quick render buttons to a minimal MP4/GIF picker.

**Architecture:** A new `server/app/render_queue.py` module owns an `asyncio.Queue` and a single worker task; `JobState` lives in an in-memory dict keyed by uuid. `POST /api/cameras/{id}/videos` enqueues and returns 202 + `job_id`. New `/api/renders` endpoints expose snapshot/lookup/cancel. The sidebar polls `/api/renders` (1.5 s active / 10 s idle / paused while tab hidden) via a shared snapshot module in `app.js` reused by camera-overview Quick-render dedupe and the library modal.

**Tech Stack:** Python 3 / FastAPI / asyncio / ffmpeg subprocess. Vanilla JS modules served from `server/app/static/v2/`. pytest with `TestClient` for backend tests.

**Spec:** `docs/superpowers/specs/2026-05-07-render-queue-and-server-panel-design.md`

---

## File map

| File                                                     | Action  | Responsibility                                                        |
| -------------------------------------------------------- | ------- | --------------------------------------------------------------------- |
| `server/app/render_queue.py`                             | Create  | `JobState` dataclass; `RenderRunner` (queue, worker, snapshot, cancel, reaper). Contains the ffmpeg execution previously inline in `main.py`. |
| `server/app/main.py`                                     | Modify  | `VideoRequest.range_preset`; lifespan wiring; rewrite `POST /videos` to enqueue; new `/api/renders` endpoints. |
| `tests/server/test_render_queue.py`                      | Create  | Unit tests for `RenderRunner` lifecycle, cancel, reaper, range_preset. |
| `tests/server/test_video_format.py`                      | Modify  | New POST contract (202 + poll until done).                             |
| `tests/server/test_videos_listing.py`                    | Modify  | Same (only the seeding test that POSTs).                              |
| `server/app/static/v2/app.js`                            | Modify  | New `rendersStore` — shared poller + subscriber API.                   |
| `server/app/static/v2/components/sidebar.js`             | Modify  | Server panel section (storage + running + queue + recent toast).      |
| `server/app/static/v2/styles.css`                        | Modify  | `.server-panel`, `.render-row`, `.cancel-btn` (+ armed state).        |
| `server/app/static/v2/views/camera.js`                   | Modify  | Replace dead Quick render buttons (lines 376–382) with inline expander; submit via fetch + dedupe via rendersStore. |
| `server/app/static/v2/views/library.js`                  | Modify  | Render modal switches from SSE consumption to poll-based progress; close-without-cancel UX. |

---

## Task 1: Scaffold `JobState` and empty `RenderRunner`

**Files:**
- Create: `server/app/render_queue.py`
- Test: `tests/server/test_render_queue.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/server/test_render_queue.py
import time
import pytest
from app.render_queue import JobState, RenderRunner


def test_jobstate_serializes_to_dict():
    job = JobState(
        id="abc123",
        camera_id="cam-x",
        format="mp4",
        fps=24,
        start_at=None,
        end_at=None,
        range_preset="24h",
        name="timelapse-x",
        queued_at=time.time(),
    )
    d = job.to_dict()
    assert d["id"] == "abc123"
    assert d["status"] == "queued"
    assert d["range_preset"] == "24h"
    assert d["percent"] is None


def test_renderrunner_constructs_with_data_dir(tmp_path):
    runner = RenderRunner(tmp_path)
    snap = runner.snapshot()
    assert snap == {"running": None, "queued": [], "recent": []}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/server/test_render_queue.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.render_queue'`

- [ ] **Step 3: Write minimal implementation**

```python
# server/app/render_queue.py
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


MAX_CONCURRENT_RENDERS = 1
RECENT_TTL_SECONDS = 60.0


@dataclass
class JobState:
    id: str
    camera_id: str
    format: str
    fps: int
    start_at: Optional[str]
    end_at: Optional[str]
    range_preset: Optional[str]
    name: str
    queued_at: float
    status: str = "queued"
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    total_frames: Optional[int] = None
    current_frame: Optional[int] = None
    percent: Optional[int] = None
    eta_seconds: Optional[int] = None
    error: Optional[str] = None
    output_path: Optional[str] = None
    cancel_requested: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


class RenderRunner:
    def __init__(self, data_dir: Path):
        self._data_dir = Path(data_dir)
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._jobs: dict[str, JobState] = {}
        self._order: list[str] = []
        self._worker: Optional[asyncio.Task] = None
        self._reaper: Optional[asyncio.Task] = None
        self._current_id: Optional[str] = None
        self._current_proc: Optional[asyncio.subprocess.Process] = None

    def snapshot(self) -> dict:
        running = self._jobs[self._current_id].to_dict() if self._current_id else None
        queued = [self._jobs[jid].to_dict() for jid in self._order]
        recent = [
            j.to_dict() for j in self._jobs.values()
            if j.status in {"done", "failed", "cancelled"}
            and j.finished_at is not None
            and (time.time() - j.finished_at) < RECENT_TTL_SECONDS
        ]
        recent.sort(key=lambda d: d["finished_at"] or 0, reverse=True)
        return {"running": running, "queued": queued, "recent": recent}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/server/test_render_queue.py -v`
Expected: PASS (both tests)

- [ ] **Step 5: Commit**

```bash
git add server/app/render_queue.py tests/server/test_render_queue.py
git commit -m "feat(render-queue): scaffold JobState and RenderRunner skeleton"
```

---

## Task 2: `enqueue` + `snapshot` reflects FIFO order

**Files:**
- Modify: `server/app/render_queue.py`
- Test: `tests/server/test_render_queue.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/server/test_render_queue.py`:

```python
def _make_job(camera_id: str = "cam-a", fmt: str = "mp4") -> JobState:
    return JobState(
        id=f"id-{camera_id}-{fmt}",
        camera_id=camera_id, format=fmt, fps=24,
        start_at=None, end_at=None, range_preset=None,
        name=f"timelapse-{camera_id}", queued_at=time.time(),
    )


def test_enqueue_two_jobs_yields_fifo_snapshot(tmp_path):
    runner = RenderRunner(tmp_path)
    j1 = _make_job("cam-a")
    j2 = _make_job("cam-b")
    assert runner.enqueue(j1) == 1
    assert runner.enqueue(j2) == 2
    snap = runner.snapshot()
    assert snap["running"] is None
    assert [q["id"] for q in snap["queued"]] == [j1.id, j2.id]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/server/test_render_queue.py::test_enqueue_two_jobs_yields_fifo_snapshot -v`
Expected: FAIL with `AttributeError: 'RenderRunner' object has no attribute 'enqueue'`

- [ ] **Step 3: Implement `enqueue`**

Add to `RenderRunner`:

```python
    def enqueue(self, job: JobState) -> int:
        if job.id in self._jobs:
            raise ValueError(f"duplicate job id: {job.id}")
        self._jobs[job.id] = job
        self._order.append(job.id)
        self._queue.put_nowait(job.id)
        return len(self._order)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/server/test_render_queue.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add server/app/render_queue.py tests/server/test_render_queue.py
git commit -m "feat(render-queue): enqueue jobs and surface FIFO order in snapshot"
```

---

## Task 3: Worker loop with stub `_run_job`

**Files:**
- Modify: `server/app/render_queue.py`
- Test: `tests/server/test_render_queue.py`

The worker pops ids from the queue, transitions state, and calls `_run_job`. We'll keep `_run_job` as a stub method here so we can test the lifecycle without ffmpeg; Tasks 6–7 fill in the real subprocess work.

- [ ] **Step 1: Write the failing test**

Append:

```python
import asyncio


@pytest.mark.asyncio
async def test_worker_runs_job_to_done(tmp_path, monkeypatch):
    runner = RenderRunner(tmp_path)

    async def fake_run(job: JobState) -> None:
        job.percent = 100
        job.output_path = "videos/cam-a/timelapse.mp4"

    monkeypatch.setattr(runner, "_run_job", fake_run)

    await runner.start()
    runner.enqueue(_make_job("cam-a"))
    # poll until terminal (or timeout)
    for _ in range(200):
        await asyncio.sleep(0.01)
        snap = runner.snapshot()
        if snap["running"] is None and snap["recent"]:
            break
    await runner.stop()

    snap = runner.snapshot()
    assert snap["recent"][0]["status"] == "done"
    assert snap["recent"][0]["output_path"] == "videos/cam-a/timelapse.mp4"
```

`pytest-asyncio` is not yet a dev dep. Add it to `requirements-dev.txt`:

```
pytest-asyncio>=0.23,<1
```

Then `pip install -r requirements-dev.txt`.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/server/test_render_queue.py::test_worker_runs_job_to_done -v`
Expected: FAIL with `AttributeError: 'RenderRunner' object has no attribute 'start'`

- [ ] **Step 3: Implement worker + lifecycle methods**

Add to `RenderRunner`:

```python
    async def start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self._worker_loop())

    async def stop(self) -> None:
        if self._current_proc and self._current_proc.returncode is None:
            self._current_proc.terminate()
            try:
                await asyncio.wait_for(self._current_proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._current_proc.kill()
                await self._current_proc.wait()
        if self._worker:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
            self._worker = None

    async def _worker_loop(self) -> None:
        while True:
            job_id = await self._queue.get()
            try:
                self._order.remove(job_id)
            except ValueError:
                pass
            job = self._jobs[job_id]
            if job.cancel_requested:
                job.status = "cancelled"
                job.finished_at = time.time()
                self._queue.task_done()
                continue
            job.status = "running"
            job.started_at = time.time()
            self._current_id = job_id
            try:
                await self._run_job(job)
                job.status = "cancelled" if job.cancel_requested else "done"
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                job.status = "failed"
                job.error = str(exc)
            finally:
                job.finished_at = time.time()
                self._current_id = None
                self._current_proc = None
                self._queue.task_done()

    async def _run_job(self, job: JobState) -> None:
        raise NotImplementedError
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/server/test_render_queue.py::test_worker_runs_job_to_done -v`
Expected: PASS

If `pytest-asyncio` complains about mode, add `asyncio_mode = "auto"` under the existing `[tool.pytest.ini_options]` section in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["server", "agent"]
addopts = "-q"
asyncio_mode = "auto"
```

- [ ] **Step 5: Commit**

```bash
git add server/app/render_queue.py tests/server/test_render_queue.py pyproject.toml
git commit -m "feat(render-queue): add worker loop and start/stop lifecycle"
```

---

## Task 4: Cancel queued and running jobs

**Files:**
- Modify: `server/app/render_queue.py`
- Test: `tests/server/test_render_queue.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
@pytest.mark.asyncio
async def test_cancel_queued_job_skips_when_popped(tmp_path, monkeypatch):
    runner = RenderRunner(tmp_path)

    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_run(job):
        started.set()
        await release.wait()

    monkeypatch.setattr(runner, "_run_job", slow_run)
    await runner.start()

    j1 = _make_job("cam-a"); j2 = _make_job("cam-b")
    runner.enqueue(j1)
    runner.enqueue(j2)
    await started.wait()                          # j1 is running
    assert await runner.cancel(j2.id) is True     # cancel queued j2
    release.set()                                 # let j1 finish
    for _ in range(200):
        await asyncio.sleep(0.01)
        if runner._current_id is None and not runner._order:
            break
    await runner.stop()

    statuses = {j.id: j.status for j in runner._jobs.values()}
    assert statuses[j1.id] == "done"
    assert statuses[j2.id] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_running_job_terminates(tmp_path, monkeypatch):
    runner = RenderRunner(tmp_path)

    started = asyncio.Event()

    class FakeProc:
        def __init__(self): self.returncode = None; self._terminated = False
        def terminate(self): self._terminated = True; self.returncode = -15
        async def wait(self): return self.returncode

    fake_proc = FakeProc()

    async def run(job):
        runner._current_proc = fake_proc  # simulate the real ffmpeg attachment
        started.set()
        # cooperatively wait for cancellation
        while not job.cancel_requested:
            await asyncio.sleep(0.01)

    monkeypatch.setattr(runner, "_run_job", run)
    await runner.start()
    j1 = _make_job("cam-a")
    runner.enqueue(j1)
    await started.wait()
    assert await runner.cancel(j1.id) is True
    for _ in range(200):
        await asyncio.sleep(0.01)
        if runner._jobs[j1.id].status == "cancelled":
            break
    await runner.stop()

    assert runner._jobs[j1.id].status == "cancelled"
    assert fake_proc._terminated is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/server/test_render_queue.py -v -k cancel`
Expected: FAIL with `AttributeError: ... 'cancel'`

- [ ] **Step 3: Implement `cancel`**

Add to `RenderRunner`:

```python
    async def cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job is None:
            return False
        if job.status in {"done", "failed", "cancelled"}:
            return True  # idempotent
        job.cancel_requested = True
        if self._current_id == job_id and self._current_proc is not None:
            if self._current_proc.returncode is None:
                self._current_proc.terminate()
        return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/server/test_render_queue.py -v -k cancel`
Expected: PASS (both)

- [ ] **Step 5: Commit**

```bash
git add server/app/render_queue.py tests/server/test_render_queue.py
git commit -m "feat(render-queue): cancel queued and running jobs"
```

---

## Task 5: Reaper evicts terminal jobs after `RECENT_TTL_SECONDS`

**Files:**
- Modify: `server/app/render_queue.py`
- Test: `tests/server/test_render_queue.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
@pytest.mark.asyncio
async def test_reaper_evicts_old_terminal_jobs(tmp_path, monkeypatch):
    runner = RenderRunner(tmp_path)
    # speed up reaper by patching the constant *and* adding a tiny tick
    monkeypatch.setattr("app.render_queue.RECENT_TTL_SECONDS", 0.05)

    j = _make_job("cam-a")
    runner._jobs[j.id] = j
    j.status = "done"
    j.finished_at = time.time() - 1.0  # already old

    await runner.start()
    await asyncio.sleep(0.2)            # one reaper cycle
    await runner.stop()

    assert j.id not in runner._jobs
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/server/test_render_queue.py::test_reaper_evicts_old_terminal_jobs -v`
Expected: FAIL — terminal job still in `_jobs`.

- [ ] **Step 3: Implement reaper**

Add a `REAPER_INTERVAL_SECONDS = 0.05` constant near the top, and to `RenderRunner`:

```python
REAPER_INTERVAL_SECONDS = 5.0   # already module-scope constant; keep this 5.0
```

Then update `start`/`stop` and add the loop:

```python
    async def start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self._worker_loop())
        if self._reaper is None:
            self._reaper = asyncio.create_task(self._reaper_loop())

    async def stop(self) -> None:
        # ...existing termination logic for proc + worker...
        if self._reaper:
            self._reaper.cancel()
            try:
                await self._reaper
            except asyncio.CancelledError:
                pass
            self._reaper = None

    async def _reaper_loop(self) -> None:
        while True:
            await asyncio.sleep(min(REAPER_INTERVAL_SECONDS, RECENT_TTL_SECONDS) / 2)
            cutoff = time.time() - RECENT_TTL_SECONDS
            stale = [
                jid for jid, j in self._jobs.items()
                if j.status in {"done", "failed", "cancelled"}
                and j.finished_at is not None
                and j.finished_at < cutoff
            ]
            for jid in stale:
                self._jobs.pop(jid, None)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/server/test_render_queue.py::test_reaper_evicts_old_terminal_jobs -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/app/render_queue.py tests/server/test_render_queue.py
git commit -m "feat(render-queue): reaper evicts terminal jobs after TTL"
```

---

## Task 6: `_run_job` for MP4 with live progress

**Files:**
- Modify: `server/app/render_queue.py`
- Test: `tests/server/test_render_queue.py`

This task moves the MP4 ffmpeg block currently in `main.py` (`generate_video`'s `event_stream`, lines ~1572–1640) into `RenderRunner._run_job`. The progress is written onto `JobState` instead of yielded as SSE.

We expose two helper methods on the runner so `main.py` can pre-compute selections without circular imports: `selected_images` and `ffmpeg_escape` stay in `main.py`; the runner takes the resolved list as part of the job. To keep `JobState` JSON-serializable, the resolved input data goes into a parallel internal map (`self._inputs[job_id]`).

- [ ] **Step 1: Add `_inputs` and `enqueue_with_inputs`**

Modify `__init__`:

```python
        self._inputs: dict[str, dict] = {}
```

Add a new public method:

```python
    def enqueue_with_inputs(
        self, job: JobState, *, list_path: Path, output_path: Path,
        total_frames: int,
    ) -> int:
        position = self.enqueue(job)
        job.total_frames = total_frames
        self._inputs[job.id] = {
            "list_path": list_path,
            "output_path": output_path,
        }
        return position
```

(Existing `enqueue` is kept for tests.)

- [ ] **Step 2: Write the failing test**

Append (uses real ffmpeg — skips when missing, like the existing format tests):

```python
import shutil


@pytest.mark.asyncio
async def test_run_job_mp4_produces_file_and_progress(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")

    cam_dir = tmp_path / "images" / "cam-a" / "2026-05-04"
    cam_dir.mkdir(parents=True)
    minimal_jpeg = bytes.fromhex(
        "ffd8ffe000104a46494600010200000100010000fffe0010"
        "4c61766335392e33372e31303000ffdb00430008040404"
        "04040505050505050606060606060606060606060607070"
        "70708080807070706060707080808080909090808080809"
        "090a0a0a0c0c0b0b0e0e0e111114ffc4004b0001010000"
        "0000000000000000000000000008010100000000000000"
        "00000000000000000010010000000000000000000000000"
        "0000000110100000000000000000000000000000000ffc0"
        "0011080002000203012200021100031100ffda000c030100"
        "02110311003f009fc007ffd9"
    )
    (cam_dir / "143000.jpg").write_bytes(minimal_jpeg)
    (cam_dir / "143005.jpg").write_bytes(minimal_jpeg)

    list_path = tmp_path / "list.txt"
    list_path.write_text(
        f"file '{cam_dir / '143000.jpg'}'\nfile '{cam_dir / '143005.jpg'}'\n"
    )
    out = tmp_path / "out.mp4"

    runner = RenderRunner(tmp_path)
    job = _make_job("cam-a", "mp4")
    runner.enqueue_with_inputs(
        job, list_path=list_path, output_path=out, total_frames=2
    )
    await runner.start()
    for _ in range(2000):
        await asyncio.sleep(0.01)
        if runner.snapshot()["running"] is None and runner.snapshot()["recent"]:
            break
    await runner.stop()

    rec = runner.snapshot()["recent"][0]
    assert rec["status"] == "done", rec
    assert out.exists()
    assert rec["percent"] == 100
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/server/test_render_queue.py::test_run_job_mp4_produces_file_and_progress -v`
Expected: FAIL with `NotImplementedError`.

- [ ] **Step 4: Implement `_run_job` (MP4 branch)**

```python
    async def _run_job(self, job: JobState) -> None:
        inputs = self._inputs.pop(job.id, None)
        if inputs is None:
            raise RuntimeError("missing inputs for job")
        list_path: Path = inputs["list_path"]
        output_path: Path = inputs["output_path"]
        try:
            if job.format == "mp4":
                await self._run_mp4(job, list_path, output_path)
            elif job.format == "gif":
                await self._run_gif(job, list_path, output_path)
            else:
                raise ValueError(f"unsupported format: {job.format}")
            if not job.cancel_requested:
                job.percent = 100
                job.output_path = str(output_path.relative_to(self._data_dir))
        finally:
            list_path.unlink(missing_ok=True)
            if job.cancel_requested and output_path.exists():
                output_path.unlink(missing_ok=True)

    async def _run_mp4(self, job: JobState, list_path: Path, output_path: Path) -> None:
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(list_path),
            "-vf", f"fps={job.fps},format=yuv420p",
            "-c:v", "libx264", "-movflags", "+faststart",
            "-progress", "pipe:1", str(output_path),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._current_proc = proc
        last_frame = 0
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").strip()
            if "=" not in text:
                continue
            key, value = text.split("=", 1)
            if key == "frame":
                try:
                    last_frame = int(value)
                except ValueError:
                    continue
                job.current_frame = last_frame
                if job.total_frames:
                    job.percent = min(100, round(last_frame / job.total_frames * 100))
                    if job.started_at and last_frame > 0:
                        elapsed = time.time() - job.started_at
                        observed = last_frame / elapsed if elapsed else 0
                        if observed > 0:
                            job.eta_seconds = max(0, int(
                                (job.total_frames - last_frame) / observed
                            ))
            elif key == "progress" and value == "end":
                break
        rc = await proc.wait()
        if rc != 0 and not job.cancel_requested:
            stderr = (await proc.stderr.read()).decode("utf-8", errors="replace").strip()
            raise RuntimeError(stderr or "ffmpeg failed")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/server/test_render_queue.py::test_run_job_mp4_produces_file_and_progress -v`
Expected: PASS (skips on hosts without ffmpeg)

- [ ] **Step 6: Commit**

```bash
git add server/app/render_queue.py tests/server/test_render_queue.py
git commit -m "feat(render-queue): MP4 job runner with live progress"
```

---

## Task 7: `_run_job` for GIF (two-pass with checkpoints)

**Files:**
- Modify: `server/app/render_queue.py`
- Test: `tests/server/test_render_queue.py`

- [ ] **Step 1: Write the failing test**

Append (reuses the JPEG-seed helper inline):

```python
@pytest.mark.asyncio
async def test_run_job_gif_produces_file(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    # reuse seeding from the mp4 test by extracting a small helper above this point
    cam_dir = tmp_path / "images" / "cam-b" / "2026-05-04"
    cam_dir.mkdir(parents=True)
    minimal_jpeg = bytes.fromhex(
        "ffd8ffe000104a46494600010200000100010000fffe0010"
        "4c61766335392e33372e31303000ffdb00430008040404"
        "04040505050505050606060606060606060606060607070"
        "70708080807070706060707080808080909090808080809"
        "090a0a0a0c0c0b0b0e0e0e111114ffc4004b0001010000"
        "0000000000000000000000000008010100000000000000"
        "00000000000000000010010000000000000000000000000"
        "0000000110100000000000000000000000000000000ffc0"
        "0011080002000203012200021100031100ffda000c030100"
        "02110311003f009fc007ffd9"
    )
    (cam_dir / "143000.jpg").write_bytes(minimal_jpeg)
    (cam_dir / "143005.jpg").write_bytes(minimal_jpeg)

    list_path = tmp_path / "list.txt"
    list_path.write_text(
        f"file '{cam_dir / '143000.jpg'}'\nfile '{cam_dir / '143005.jpg'}'\n"
    )
    out = tmp_path / "out.gif"

    runner = RenderRunner(tmp_path)
    job = _make_job("cam-b", "gif")
    runner.enqueue_with_inputs(
        job, list_path=list_path, output_path=out, total_frames=2
    )
    await runner.start()
    for _ in range(3000):
        await asyncio.sleep(0.01)
        if runner.snapshot()["running"] is None and runner.snapshot()["recent"]:
            break
    await runner.stop()

    rec = runner.snapshot()["recent"][0]
    assert rec["status"] == "done", rec
    assert out.exists()
    assert rec["percent"] == 100
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/server/test_render_queue.py::test_run_job_gif_produces_file -v`
Expected: FAIL with `unsupported format: gif`.

- [ ] **Step 3: Implement `_run_gif`**

Add to `RenderRunner`:

```python
    async def _run_gif(self, job: JobState, list_path: Path, output_path: Path) -> None:
        palette = output_path.with_suffix(".palette.png")
        try:
            palette_cmd = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "concat", "-safe", "0", "-i", str(list_path),
                "-vf", f"fps={job.fps},scale=720:-1:flags=lanczos,palettegen",
                str(palette),
            ]
            proc = await asyncio.create_subprocess_exec(
                *palette_cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
            )
            self._current_proc = proc
            rc = await proc.wait()
            if rc != 0:
                if job.cancel_requested:
                    return
                err = (await proc.stderr.read()).decode("utf-8", errors="replace").strip()
                raise RuntimeError(err or "ffmpeg palettegen failed")
            job.percent = 50

            if job.cancel_requested:
                return

            encode_cmd = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "concat", "-safe", "0", "-i", str(list_path),
                "-i", str(palette),
                "-filter_complex",
                f"fps={job.fps},scale=720:-1:flags=lanczos[x];[x][1:v]paletteuse",
                str(output_path),
            ]
            proc = await asyncio.create_subprocess_exec(
                *encode_cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
            )
            self._current_proc = proc
            rc = await proc.wait()
            if rc != 0:
                if job.cancel_requested:
                    return
                err = (await proc.stderr.read()).decode("utf-8", errors="replace").strip()
                raise RuntimeError(err or "ffmpeg gif encode failed")
        finally:
            palette.unlink(missing_ok=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/server/test_render_queue.py::test_run_job_gif_produces_file -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/app/render_queue.py tests/server/test_render_queue.py
git commit -m "feat(render-queue): GIF job runner with two-pass checkpoints"
```

---

## Task 8: `range_preset` resolution + filename uniqueness suffix

**Files:**
- Modify: `server/app/render_queue.py`, `server/app/main.py`
- Test: `tests/server/test_render_queue.py`

The runner exposes a small pure helper for the API layer to call.

- [ ] **Step 1: Write the failing test**

Append:

```python
from datetime import datetime, timezone
from app.render_queue import resolve_range_preset, unique_video_stem


def test_range_preset_24h(monkeypatch):
    fixed = datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc)
    start, end = resolve_range_preset("24h", now=fixed)
    assert end == "2026-05-07T12:00:00+00:00"
    assert start == "2026-05-06T12:00:00+00:00"


def test_range_preset_all_returns_none():
    assert resolve_range_preset("all") == (None, None)


def test_range_preset_unknown_raises():
    with pytest.raises(ValueError):
        resolve_range_preset("month")


def test_unique_video_stem_appends_suffix():
    stem = unique_video_stem(timestamp="20260507T120000Z", job_id="abcdef1234")
    assert stem == "timelapse-20260507T120000Z-abcdef"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/server/test_render_queue.py -v -k range_preset`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement helpers**

Append to `server/app/render_queue.py`:

```python
from datetime import datetime, timedelta, timezone

_RANGE_PRESETS = {
    "24h": timedelta(days=1),
    "7d":  timedelta(days=7),
}


def resolve_range_preset(
    preset: str, *, now: Optional[datetime] = None
) -> tuple[Optional[str], Optional[str]]:
    if preset == "all":
        return None, None
    if preset not in _RANGE_PRESETS:
        raise ValueError(f"unknown range_preset: {preset}")
    end = (now or datetime.now(timezone.utc))
    start = end - _RANGE_PRESETS[preset]
    return start.isoformat(), end.isoformat()


def unique_video_stem(*, timestamp: str, job_id: str) -> str:
    return f"timelapse-{timestamp}-{job_id[:6]}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/server/test_render_queue.py -v -k 'range_preset or unique_video_stem'`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add server/app/render_queue.py tests/server/test_render_queue.py
git commit -m "feat(render-queue): range_preset resolver and unique filename stem"
```

---

## Task 9: FastAPI lifespan wiring (singleton runner)

**Files:**
- Modify: `server/app/main.py`

- [ ] **Step 1: Add a module-level accessor**

Near the top of `server/app/main.py` (after imports):

```python
from app.render_queue import RenderRunner

_runner: Optional[RenderRunner] = None


def get_runner() -> RenderRunner:
    if _runner is None:
        raise RuntimeError("RenderRunner not started")
    return _runner
```

- [ ] **Step 2: Wire startup/shutdown**

Find the existing FastAPI app construction. If a `lifespan` is already used, add to it; otherwise add `@app.on_event` handlers — match what's already there. Insert:

```python
@app.on_event("startup")
async def _start_render_runner() -> None:
    global _runner
    _runner = RenderRunner(DATA_DIR)
    await _runner.start()


@app.on_event("shutdown")
async def _stop_render_runner() -> None:
    global _runner
    if _runner is not None:
        await _runner.stop()
        _runner = None
```

- [ ] **Step 3: Smoke-check the import path with the existing test suite**

Run: `pytest tests/server/test_smoke.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add server/app/main.py
git commit -m "feat(render-queue): wire RenderRunner singleton into FastAPI lifespan"
```

---

## Task 10: Rewrite `POST /api/cameras/{id}/videos` to enqueue

**Files:**
- Modify: `server/app/main.py`
- Modify: `tests/server/test_video_format.py`

- [ ] **Step 1: Add `range_preset` to `VideoRequest`**

In `server/app/main.py`, update the model (around line 410):

```python
class VideoRequest(BaseModel):
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    fps: int = Field(24, ge=1, le=60)
    name: Optional[str] = None
    format: str = Field("mp4", pattern=r"^(mp4|gif)$")
    range_preset: Optional[str] = Field(None, pattern=r"^(24h|7d|all)$")
```

- [ ] **Step 2: Replace `generate_video` body**

Replace the `@app.post("/api/cameras/{camera_id}/videos")` handler (currently lines ~1535–1661) with the synchronous-validation + enqueue version:

```python
import uuid
from app.render_queue import (
    JobState, resolve_range_preset, unique_video_stem,
)


@app.post("/api/cameras/{camera_id}/videos", status_code=202)
async def generate_video(camera_id: str, request: VideoRequest) -> dict:
    camera_id = safe_identifier(camera_id)

    # selected_images() in main.py compares YYYY-MM-DD prefixes against
    # path.parent.name (see main.py:884–893). Day-granular precision is the
    # truth on disk, so resolve presets to date strings, not full ISO timestamps.
    # Practical effect: "Last 24 hours" = "frames from yesterday's date or
    # later" — slightly wider than literally 24h, which is fine for a quick
    # render preset.
    if request.range_preset:
        start_iso, end_iso = resolve_range_preset(request.range_preset)
        request.start_date = (start_iso or "")[:10] or None
        request.end_date   = (end_iso   or "")[:10] or None

    images = selected_images(camera_id, request)
    if not images:
        raise HTTPException(status_code=404, detail="No images found for selection")
    if not shutil.which("ffmpeg"):
        raise HTTPException(status_code=500, detail="ffmpeg is not installed")

    video_dir = DATA_DIR / "videos" / camera_id
    video_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    job_id = uuid.uuid4().hex
    if request.name:
        try:
            stem = safe_identifier(request.name)
        except HTTPException as error:
            raise HTTPException(status_code=400, detail=f"Invalid video name: {error.detail}") from error
    else:
        stem = unique_video_stem(timestamp=timestamp, job_id=job_id)

    output_path = video_dir / f"{stem}.{request.format}"
    list_path   = video_dir / f"{stem}.txt"

    with list_path.open("w", encoding="utf-8") as list_file:
        for path in images:
            list_file.write(f"file '{ffmpeg_escape(path)}'\n")

    job = JobState(
        id=job_id, camera_id=camera_id,
        format=request.format, fps=request.fps,
        start_at=request.start_date, end_at=request.end_date,
        range_preset=request.range_preset,
        name=stem, queued_at=time.time(),
    )
    position = get_runner().enqueue_with_inputs(
        job, list_path=list_path, output_path=output_path,
        total_frames=len(images),
    )
    return {"job_id": job_id, "status": "queued", "position": position}
```

(Remove the now-dead `event_stream` body, the SSE `StreamingResponse`, and the legacy `run_ffmpeg_mp4`/`run_ffmpeg_gif` helpers in `main.py` — they've moved to the runner. Leave `selected_images` and `ffmpeg_escape` where they are.)

- [ ] **Step 3: Update the existing format tests**

Replace the body of `tests/server/test_video_format.py` so each test polls `/api/renders/{job_id}` until terminal. Updated test (replace `parse_sse_done` and the test bodies):

```python
import shutil, time
from pathlib import Path
import pytest


@pytest.fixture(autouse=True)
def _seed_two_jpegs(tmp_data_dir):
    cam_dir = Path(tmp_data_dir) / "images" / "cam-vids" / "2026-05-04"
    cam_dir.mkdir(parents=True, exist_ok=True)
    minimal_jpeg = bytes.fromhex(
        "ffd8ffe000104a46494600010200000100010000fffe0010"
        "4c61766335392e33372e31303000ffdb00430008040404"
        "04040505050505050606060606060606060606060607070"
        "70708080807070706060707080808080909090808080809"
        "090a0a0a0c0c0b0b0e0e0e111114ffc4004b0001010000"
        "0000000000000000000000000008010100000000000000"
        "00000000000000000010010000000000000000000000000"
        "0000000110100000000000000000000000000000000ffc0"
        "0011080002000203012200021100031100ffda000c030100"
        "02110311003f009fc007ffd9"
    )
    (cam_dir / "143000.jpg").write_bytes(minimal_jpeg)
    (cam_dir / "143005.jpg").write_bytes(minimal_jpeg)


def _wait_for_terminal(client, job_id: str, timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/api/renders/{job_id}")
        assert r.status_code == 200, r.text
        body = r.json()
        if body["status"] in {"done", "failed", "cancelled"}:
            return body
        time.sleep(0.1)
    raise AssertionError(f"job {job_id} did not terminate within {timeout}s")


def _enqueue_and_wait(client, payload: dict) -> dict:
    r = client.post("/api/cameras/cam-vids/videos", json=payload)
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    return _wait_for_terminal(client, job_id)


def test_default_format_is_mp4(client):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    body = _enqueue_and_wait(client, {"start_date": "2026-05-04", "end_date": "2026-05-04", "fps": 12})
    assert body["status"] == "done"
    assert body["output_path"].endswith(".mp4")


def test_explicit_mp4_format(client):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    body = _enqueue_and_wait(client, {"start_date": "2026-05-04", "end_date": "2026-05-04", "fps": 12, "format": "mp4"})
    assert body["output_path"].endswith(".mp4")


def test_gif_format(client):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    body = _enqueue_and_wait(client, {"start_date": "2026-05-04", "end_date": "2026-05-04", "fps": 12, "format": "gif"})
    assert body["output_path"].endswith(".gif")


def test_invalid_format_rejected(client):
    r = client.post("/api/cameras/cam-vids/videos",
                    json={"start_date": "2026-05-04", "end_date": "2026-05-04", "fps": 12, "format": "webm"})
    assert r.status_code == 422


def test_gif_can_be_downloaded(client):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    body = _enqueue_and_wait(client, {"start_date": "2026-05-04", "end_date": "2026-05-04", "fps": 12, "format": "gif"})
    filename = body["output_path"].split("/")[-1]
    r = client.get(f"/api/cameras/cam-vids/videos/{filename}")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/gif"


def test_mp4_can_be_downloaded(client):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    body = _enqueue_and_wait(client, {"start_date": "2026-05-04", "end_date": "2026-05-04", "fps": 12, "format": "mp4"})
    filename = body["output_path"].split("/")[-1]
    r = client.get(f"/api/cameras/cam-vids/videos/{filename}")
    assert r.status_code == 200
    assert r.headers["content-type"] == "video/mp4"


def test_unsupported_extension_returns_404(client):
    r = client.get("/api/cameras/cam-vids/videos/foo.webm")
    assert r.status_code == 404
```

This requires Task 11 to be done in the same commit so `/api/renders/{id}` exists. Include Task 11's endpoint in this step.

- [ ] **Step 4: Run tests (interleaved with Task 11 below if you implemented serially — see Task 11 first if you haven't)**

Run: `pytest tests/server/test_video_format.py -v`
Expected: tests skip without ffmpeg; pass with ffmpeg.

- [ ] **Step 5: Commit (combined with Task 11 if executing in order)**

```bash
git add server/app/main.py tests/server/test_video_format.py
git commit -m "feat(render-queue): POST /videos enqueues and returns 202+job_id"
```

---

## Task 11: GET `/api/renders` and `/api/renders/{job_id}`

**Files:**
- Modify: `server/app/main.py`
- Test: `tests/server/test_render_queue.py` (route-level tests via TestClient)

- [ ] **Step 1: Add the endpoints**

Append to `server/app/main.py`:

```python
@app.get("/api/renders")
def list_renders() -> dict:
    return get_runner().snapshot()


@app.get("/api/renders/{job_id}")
def get_render(job_id: str) -> dict:
    runner = get_runner()
    job = runner._jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Render not found")
    return job.to_dict()
```

(Reading `_jobs` directly here is acceptable — `RenderRunner` is a same-process singleton. Alternatively expose a `runner.get(job_id)` accessor; do that if you prefer.)

- [ ] **Step 2: Write a test**

Append to `tests/server/test_render_queue.py`:

```python
def test_renders_endpoint_returns_snapshot(client):
    r = client.get("/api/renders")
    assert r.status_code == 200
    body = r.json()
    assert body == {"running": None, "queued": [], "recent": []}


def test_render_lookup_404_for_unknown_job(client):
    r = client.get("/api/renders/does-not-exist")
    assert r.status_code == 404
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/server/test_render_queue.py -v -k 'renders_endpoint or render_lookup'`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add server/app/main.py tests/server/test_render_queue.py
git commit -m "feat(render-queue): expose /api/renders snapshot and lookup"
```

---

## Task 12: DELETE `/api/renders/{job_id}` (cancel)

**Files:**
- Modify: `server/app/main.py`
- Test: `tests/server/test_render_queue.py`

- [ ] **Step 1: Add the endpoint**

Append to `server/app/main.py`:

```python
@app.delete("/api/renders/{job_id}", status_code=204)
async def cancel_render(job_id: str):
    ok = await get_runner().cancel(job_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Render not found")
    return None
```

- [ ] **Step 2: Write a test**

Append:

```python
def test_cancel_unknown_job_returns_404(client):
    r = client.delete("/api/renders/does-not-exist")
    assert r.status_code == 404
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/server/test_render_queue.py::test_cancel_unknown_job_returns_404 -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add server/app/main.py tests/server/test_render_queue.py
git commit -m "feat(render-queue): DELETE /api/renders cancels jobs"
```

---

## Task 13: Update `tests/server/test_videos_listing.py` (only the test that POSTs)

**Files:**
- Modify: `tests/server/test_videos_listing.py`

The current file mostly seeds files directly (no POST), so most tests already pass. Only check; no test changes needed unless one POSTs.

- [ ] **Step 1: Run the existing test file as-is**

Run: `pytest tests/server/test_videos_listing.py -v`
Expected: All pass.

- [ ] **Step 2: If anything fails referencing SSE, update it the same way as Task 10**

If the tests still pass, skip. If something does POST to `/videos`, mirror the `_enqueue_and_wait` helper from Task 10.

- [ ] **Step 3: Commit (only if changes were needed)**

```bash
git add tests/server/test_videos_listing.py
git commit -m "test(render-queue): adapt videos-listing tests to enqueue contract"
```

---

## Task 14: Shared `rendersStore` module in `app.js`

**Files:**
- Modify: `server/app/static/v2/app.js`

A single browser-side store polls `/api/renders` and notifies subscribers. Used by sidebar, camera Quick render, and the library modal.

- [ ] **Step 1: Add the store to `app.js`**

Append (use the file's existing module style — check the top of the file for `export` patterns):

```javascript
// Shared poller for /api/renders. Adapts cadence to activity and pauses while
// the tab is hidden. Subscribers receive the latest snapshot on change.
const ACTIVE_INTERVAL_MS = 1500;
const IDLE_INTERVAL_MS   = 10000;

export const rendersStore = (() => {
    let snapshot = { running: null, queued: [], recent: [] };
    const subs = new Set();
    let timer = null;
    let inflight = false;

    const isActive = () =>
        snapshot.running !== null || (snapshot.queued && snapshot.queued.length > 0);

    async function tick() {
        if (document.visibilityState !== "visible") {
            schedule(IDLE_INTERVAL_MS);
            return;
        }
        if (inflight) {
            schedule(isActive() ? ACTIVE_INTERVAL_MS : IDLE_INTERVAL_MS);
            return;
        }
        inflight = true;
        try {
            const r = await fetch("/api/renders", { headers: { Accept: "application/json" } });
            if (r.ok) {
                snapshot = await r.json();
                subs.forEach((cb) => { try { cb(snapshot); } catch (_) {} });
            }
        } catch (_) { /* swallow; retry on next tick */ }
        finally {
            inflight = false;
            schedule(isActive() ? ACTIVE_INTERVAL_MS : IDLE_INTERVAL_MS);
        }
    }

    function schedule(ms) {
        if (timer) clearTimeout(timer);
        timer = setTimeout(tick, ms);
    }

    document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "visible") {
            schedule(0);
        }
    });

    // start on first import
    schedule(0);

    return {
        getSnapshot: () => snapshot,
        subscribe: (cb) => { subs.add(cb); cb(snapshot); return () => subs.delete(cb); },
        async cancel(jobId) {
            await fetch(`/api/renders/${encodeURIComponent(jobId)}`, { method: "DELETE" });
            schedule(0);
        },
    };
})();
```

- [ ] **Step 2: Manual smoke test**

Start the server (`uvicorn app.main:app --port 8081`), open the dashboard, open devtools console:

```javascript
import("/static/v2/app.js").then(m => m.rendersStore.subscribe(s => console.log(s)));
```

Expected: console logs `{running: null, queued: [], recent: []}` initially and on each poll.

- [ ] **Step 3: Commit**

```bash
git add server/app/static/v2/app.js
git commit -m "feat(ui): shared rendersStore poller for /api/renders"
```

---

## Task 15: Sidebar server panel — HTML scaffold

**Files:**
- Modify: `server/app/static/v2/components/sidebar.js`

Replace the `storageMeter()` call with a fuller `serverPanel()` that subscribes to `rendersStore`.

- [ ] **Step 1: Replace `storageMeter()` and update render**

Edit `server/app/static/v2/components/sidebar.js`:

```javascript
import { api, escapeHtml, icon, statusKind, formatBytes, rendersStore } from "/static/v2/app.js";

// ...keep existing helpers...

function fmtRange(job) {
    if (job.range_preset === "all") return "all time";
    if (job.range_preset === "24h") return "24h";
    if (job.range_preset === "7d")  return "7d";
    if (job.start_at && job.end_at) return `${job.start_at.slice(0,10)} → ${job.end_at.slice(0,10)}`;
    return "—";
}

function fmtEta(seconds) {
    if (seconds == null) return "";
    const m = Math.floor(seconds / 60), s = seconds % 60;
    return `~${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")} left`;
}

function renderRow(job, kind) {
    const base = `${escapeHtml(job.camera_id)} · ${job.format.toUpperCase()} · ${fmtRange(job)}`;
    if (kind === "running") {
        const pct = job.percent ?? 0;
        const eta = fmtEta(job.eta_seconds);
        const frames = job.total_frames
            ? `${(job.current_frame || 0).toLocaleString()} / ${job.total_frames.toLocaleString()} frames`
            : "";
        const cancelling = job.cancel_requested ? "(cancelling…)" : "";
        return `
          <div class="render-row running">
            <div class="render-row-head">▶ Rendering · ${escapeHtml(job.camera_id)} · ${job.format.toUpperCase()}</div>
            <div class="render-bar"><span style="width:${pct}%"></span></div>
            <div class="render-meta">
              <span>${pct}% ${eta}</span>
              <button class="cancel-btn" data-cancel="${job.id}" data-kind="running" aria-label="Cancel">✕</button>
            </div>
            <div class="render-meta small">${frames} ${cancelling}</div>
          </div>`;
    }
    if (kind === "queued") {
        return `
          <div class="render-row queued">
            <div class="render-meta">
              <span>${base}</span>
              <button class="cancel-btn" data-cancel="${job.id}" data-kind="queued" aria-label="Remove from queue">✕</button>
            </div>
          </div>`;
    }
    // recent
    const ico = job.status === "done" ? "✓" : job.status === "failed" ? "✕" : "⊘";
    const tip = job.error ? ` title="${escapeHtml(job.error)}"` : "";
    return `<div class="render-row recent" data-recent="${job.id}"${tip}>${ico} ${base}</div>`;
}

function serverPanel(stats, snap) {
    const used = stats?.storage_bytes ?? 0;
    const cap  = stats?.storage_capacity_bytes ?? (used * 2 || 1);
    const pct  = Math.min(100, Math.round((used / cap) * 100));
    const queue = snap.queued || [];
    const recent = (snap.recent || [])[0];
    return `
      <section class="server-panel">
        <div class="lbl">SERVER</div>

        <div class="server-row storage">
          <div class="row" style="gap:6px">${icon("server", 12)} <span class="lbl ink small">Storage</span></div>
          <div class="num small">${formatBytes(used)} / ${formatBytes(cap)}</div>
          <div class="storage-bar"><span style="width:${pct}%"></span></div>
        </div>

        ${snap.running ? renderRow(snap.running, "running") : ""}

        ${queue.length ? `
          <div class="queue-head between">
            <span class="lbl small">⌛ Queue · ${queue.length}</span>
          </div>
          ${queue.map(j => renderRow(j, "queued")).join("")}
        ` : ""}

        ${recent ? renderRow(recent, "recent") : ""}
      </section>
    `;
}

// Module-level state: latest snapshot + armed cancel ids that survive rerenders.
let latestSnap = { running: null, queued: [], recent: [] };
const armedCancels = new Set(); // job ids that the user has armed but not committed

rendersStore.subscribe((s) => {
    latestSnap = s;
    // Surgical update: replace ONLY the server panel's DOM, not the whole sidebar.
    // Rebuilding the full sidebar at the active 1.5s cadence would blow away
    // search input focus, scroll position, and rerun the cameras fetch.
    const existing = document.querySelector("#sidebar .server-panel");
    if (existing) {
        const wrapper = document.createElement("div");
        wrapper.innerHTML = serverPanel(cachedStats, s).trim();
        const replacement = wrapper.firstElementChild;
        existing.replaceWith(replacement);
        wireServerPanel(replacement);  // re-attach handlers + restore armed state
    }
});
```

Then in `renderSidebar`, replace `${storageMeter()}` with `${serverPanel(cachedStats, latestSnap)}`, and after setting `root.innerHTML = …`, locate the panel and call `wireServerPanel(panel)` (defined in Task 17 — for Task 15 just include the function as a no-op stub so the call site exists).

- [ ] **Step 2: Manual smoke test**

Reload the dashboard. Confirm the panel still shows storage. Sidebar still functions.

- [ ] **Step 3: Commit**

```bash
git add server/app/static/v2/components/sidebar.js
git commit -m "feat(ui): server panel scaffold replaces storage strip"
```

---

## Task 16: Sidebar server panel — CSS

**Files:**
- Modify: `server/app/static/v2/styles.css`

- [ ] **Step 1: Replace storage-meter rules and add panel rules**

Replace lines 401–403 of `styles.css` with:

```css
.server-panel {
    position: absolute;
    bottom: 14px; left: 14px; right: 14px;
    padding: 10px;
    background: var(--panel-2);
    border: 1px solid var(--border);
    border-radius: 6px;
    display: flex; flex-direction: column; gap: 8px;
}
.server-panel .lbl { font-size: 9px; }
.server-panel .server-row { display: flex; flex-direction: column; gap: 2px; }
.storage-bar { height: 3px; background: var(--border); border-radius: 2px; overflow: hidden; }
.storage-bar > span { display: block; height: 100%; background: var(--accent-2); }

.render-row { display: flex; flex-direction: column; gap: 4px; padding: 6px; border-radius: 4px; background: rgba(255,255,255,0.02); }
.render-row.running { background: rgba(0,180,140,0.06); border: 1px solid rgba(0,180,140,0.18); }
.render-row .render-row-head { font-size: 11px; color: var(--ink); }
.render-row .render-meta { display: flex; justify-content: space-between; align-items: center; font-size: 10px; color: var(--soft); }
.render-row .render-meta.small { font-size: 9px; color: var(--softer); }
.render-bar { height: 3px; background: var(--border); border-radius: 2px; overflow: hidden; }
.render-bar > span { display: block; height: 100%; background: var(--green, #00b48c); transition: width .3s; }

.cancel-btn { background: transparent; border: 1px solid transparent; color: var(--soft); cursor: pointer; padding: 0 4px; border-radius: 3px; font-size: 11px; }
.cancel-btn:hover { color: var(--ink); border-color: var(--border); }
.cancel-btn.armed { color: var(--red, #ff5b5b); border-color: var(--red, #ff5b5b); }

.render-row.recent { font-size: 10px; color: var(--soft); padding: 4px 6px; opacity: 0.85; }
.queue-head { padding: 0 4px; }
```

- [ ] **Step 2: Manual smoke test**

Reload the page. Confirm the panel renders cleanly with a storage bar identical to today.

- [ ] **Step 3: Commit**

```bash
git add server/app/static/v2/styles.css
git commit -m "style(ui): server panel and render-row styling"
```

---

## Task 17: Sidebar cancel UX (armed → confirmed)

**Files:**
- Modify: `server/app/static/v2/components/sidebar.js`

- [ ] **Step 1: Replace the `wireServerPanel` stub from Task 15 with the real cancel-arm logic**

The panel is rerendered at the active poll cadence, so per-element timers don't survive. Track armed `job_id`s in the module-level `armedCancels` Set (added in Task 15) and restore the armed visual on every rewire.

```javascript
function wireServerPanel(panel) {
    if (!panel) return;
    // Restore armed visual state for any job that was armed before the rerender
    panel.querySelectorAll(".cancel-btn").forEach((btn) => {
        const id = btn.dataset.cancel;
        if (armedCancels.has(id)) {
            btn.classList.add("armed");
            btn.textContent = "Cancel?";
        }
    });
    // Wire clicks: first click arms (1.5s window), second click commits.
    panel.querySelectorAll(".cancel-btn").forEach((btn) => {
        btn.addEventListener("click", (e) => {
            e.preventDefault(); e.stopPropagation();
            const id = btn.dataset.cancel;
            if (armedCancels.has(id)) {
                armedCancels.delete(id);
                btn.disabled = true; btn.textContent = "…";
                rendersStore.cancel(id).catch(() => {});
                return;
            }
            armedCancels.add(id);
            btn.classList.add("armed");
            btn.textContent = "Cancel?";
            setTimeout(() => {
                armedCancels.delete(id);
                // The DOM may have been replaced — only update if still attached
                if (btn.isConnected) {
                    btn.classList.remove("armed");
                    btn.textContent = "✕";
                }
            }, 1500);
        });
    });
}
```

Since this replaces the Task 15 stub, no other call sites change. Confirm `wireServerPanel` is invoked in both:
1. `renderSidebar` (after `root.innerHTML = …`).
2. The `rendersStore.subscribe` callback in Task 15, after the surgical replace.

- [ ] **Step 2: Manual smoke test**

Start an MP4 render via Quick render (will be wired in Task 18, so for now POST manually with curl/devtools); verify ✕ click arms then commits.

- [ ] **Step 3: Commit**

```bash
git add server/app/static/v2/components/sidebar.js
git commit -m "feat(ui): sidebar cancel arms on first click, commits on second"
```

---

## Task 18: Quick render inline expansion in `camera.js`

**Files:**
- Modify: `server/app/static/v2/views/camera.js`

- [ ] **Step 1: Replace the dead Quick render markup**

In `renderOverview()` (lines 376–382), replace:

```javascript
<div class="card"><div class="card-b">
  <div class="lbl">Quick render</div>
  <div class="col" style="gap:8px;margin-top:8px">
    <button class="btn" data-render-range="1">Last 24 hours${icon("arrow", 12)}</button>
    <button class="btn" data-render-range="7">Last 7 days${icon("arrow", 12)}</button>
    <button class="btn" data-render-range="all">All time${icon("arrow", 12)}</button>
  </div>
</div></div>
```

with:

```javascript
<div class="card"><div class="card-b">
  <div class="lbl">Quick render</div>
  <div class="col quick-render" style="gap:8px;margin-top:8px">
    <div class="quick-row" data-preset="24h">
      <button class="btn quick-label">Last 24 hours${icon("arrow", 12)}</button>
      <div class="quick-expand" hidden>
        <div class="seg quick-format">
          <button data-fmt="mp4" class="active">MP4</button>
          <button data-fmt="gif">GIF</button>
        </div>
        <button class="btn primary quick-go">Render</button>
      </div>
    </div>
    <div class="quick-row" data-preset="7d">
      <button class="btn quick-label">Last 7 days${icon("arrow", 12)}</button>
      <div class="quick-expand" hidden>
        <div class="seg quick-format">
          <button data-fmt="mp4" class="active">MP4</button>
          <button data-fmt="gif">GIF</button>
        </div>
        <button class="btn primary quick-go">Render</button>
      </div>
    </div>
    <div class="quick-row" data-preset="all">
      <button class="btn quick-label">All time${icon("arrow", 12)}</button>
      <div class="quick-expand" hidden>
        <div class="seg quick-format">
          <button data-fmt="mp4" class="active">MP4</button>
          <button data-fmt="gif">GIF</button>
        </div>
        <button class="btn primary quick-go">Render</button>
      </div>
    </div>
  </div>
</div></div>
```

- [ ] **Step 2: Wire expansion + submit + dedupe**

Find where `renderOverview()`'s tab-handlers are wired (look for `if (tab === "overview")` block in the existing tab-switch code and the section that calls `body.innerHTML = renderOverview();`). Add a `wireOverview()` call mirroring the existing `wireRenders()` pattern, and define:

```javascript
function wireOverview() {
    const root = document.querySelector(".quick-render");
    if (!root) return;

    root.querySelectorAll(".quick-row").forEach((row) => {
        const expand = row.querySelector(".quick-expand");
        const label = row.querySelector(".quick-label");
        label.addEventListener("click", () => {
            // collapse siblings
            root.querySelectorAll(".quick-row").forEach((r) => {
                if (r !== row) r.querySelector(".quick-expand").hidden = true;
            });
            expand.hidden = !expand.hidden;
        });
        row.querySelectorAll("[data-fmt]").forEach((btn) => {
            btn.addEventListener("click", () => {
                row.querySelectorAll("[data-fmt]").forEach((b) => b.classList.remove("active"));
                btn.classList.add("active");
            });
        });
        row.querySelector(".quick-go").addEventListener("click", async () => {
            const preset = row.dataset.preset;
            const format = row.querySelector("[data-fmt].active").dataset.fmt;
            // dedupe: same camera+preset+format already running/queued?
            const snap = rendersStore.getSnapshot();
            const ids = [snap.running, ...snap.queued].filter(Boolean);
            const dup = ids.find((j) =>
                j.camera_id === camera.camera_id && j.range_preset === preset && j.format === format
            );
            const goBtn = row.querySelector(".quick-go");
            if (dup) {
                goBtn.disabled = true; goBtn.textContent = "Already queued";
                setTimeout(() => { goBtn.disabled = false; goBtn.textContent = "Render"; }, 1500);
                return;
            }
            goBtn.disabled = true; goBtn.textContent = "Queueing…";
            try {
                const r = await fetch(`/api/cameras/${encId}/videos`, {
                    method: "POST",
                    headers: { "content-type": "application/json" },
                    body: JSON.stringify({ format, fps: 24, range_preset: preset }),
                });
                if (!r.ok) {
                    const err = await r.json().catch(() => ({}));
                    goBtn.textContent = err.detail || "Failed";
                    setTimeout(() => { goBtn.disabled = false; goBtn.textContent = "Render"; }, 2000);
                    return;
                }
                goBtn.textContent = "Queued ✓";
                setTimeout(() => { expand.hidden = true; goBtn.disabled = false; goBtn.textContent = "Render"; }, 1200);
            } catch (e) {
                goBtn.disabled = false; goBtn.textContent = "Render";
            }
        });
    });
}
```

The variable `camera` should be the camera in scope (search for it in `renderCamera`); `encId` already exists in the file.

Add to imports at the top: `rendersStore` from `/static/v2/app.js`.

- [ ] **Step 3: Add a small CSS rule**

Append to `styles.css`:

```css
.quick-row { display: flex; flex-direction: column; gap: 6px; }
.quick-row .quick-expand { display: flex; gap: 8px; align-items: center; padding: 4px 0 0; }
.quick-row .quick-expand[hidden] { display: none; }
```

- [ ] **Step 4: Manual smoke test**

Click "Last 24 hours" → row expands → MP4 selected → Render → row collapses, sidebar shows render activity.

- [ ] **Step 5: Commit**

```bash
git add server/app/static/v2/views/camera.js server/app/static/v2/styles.css
git commit -m "feat(ui): wire Quick render with inline MP4/GIF picker and dedupe"
```

---

## Task 19: Library "New render" modal — poll-based progress

**Files:**
- Modify: `server/app/static/v2/views/library.js`

The existing modal currently does `fetch(...)` and reads the SSE response stream. Replace that with: POST → read `job_id` → poll `/api/renders/{job_id}` until terminal.

- [ ] **Step 1: Hook close into the polling loop via `openModal({ onClose })`**

The current modal at `library.js:140` is created with `openModal(html())`, no opts. `openModal` (in `app.js:164–186`) already supports `{ onClose }`. Change the call to:

```javascript
let active = true;
modal = openModal(html(), { onClose: () => { active = false; } });
```

Hoist `active` to the top of `openRenderModal` alongside `modal`, `fps`, etc., so both `wire()` and the close callback see it. (Don't reuse the `busy` flag — `busy` controls UI state, `active` controls the polling loop.)

- [ ] **Step 2: Replace the SSE-reading block in the submit handler**

Find the submit handler (around `library.js:188–239`). Note: today the body sends `start_at`/`end_at` which the server's `VideoRequest` doesn't declare and silently drops (Pydantic ignores unknown by default), so date filtering currently doesn't work from the modal. The migration fixes this by sending `start_date`/`end_date` (the server's actual fields, see `main.py:411–412`).

Replace the inline SSE-reading body with:

```javascript
modal.root
    .querySelector("#r-go")
    ?.addEventListener("click", async () => {
        busy = true; progress = 0; message = "";
        rerender();
        try {
            const response = await fetch(`/api/cameras/${encId}/videos`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    start_date: startDate, end_date: endDate, fps, format,
                }),
            });
            if (!response.ok) {
                const err = await response.json().catch(() => ({}));
                throw new Error(err.detail || "Failed to enqueue render");
            }
            const { job_id } = await response.json();

            // Poll until terminal — this loop is interrupted by the modal's
            // onClose callback flipping `active` to false. Closing the modal
            // does NOT cancel the job; it just stops the modal from watching.
            while (active) {
                await new Promise((r) => setTimeout(r, 1500));
                if (!active) return;
                const r = await fetch(`/api/renders/${encodeURIComponent(job_id)}`);
                if (!r.ok) throw new Error("Lost track of render");
                const job = await r.json();
                progress = job.percent ?? progress;
                if (job.status === "done") {
                    busy = false; progress = 100;
                    message = `Rendered ${job.output_path}`;
                    rerender();
                    return;
                }
                if (job.status === "failed") {
                    throw new Error(job.error || "Render failed");
                }
                if (job.status === "cancelled") {
                    busy = false; message = "Cancelled.";
                    rerender();
                    return;
                }
                rerender();
            }
        } catch (e) {
            busy = false;
            message = "Error: " + e.message;
            rerender();
        }
    });
```

The existing `busy`/`progress`/`message`/`rerender` variables and helpers stay; only the network/streaming portion changes.

- [ ] **Step 2: Add the "running in background" footer copy**

In the rendering modal state, add a small footer line:

> "You can close this — the render will keep going. Manage it from the sidebar."

(One sentence, in the same `.small` style as other modal hints.)

- [ ] **Step 3: Manual smoke test**

Open the library, click "New render", fill the form, submit:
- Progress bar updates in the modal *and* in the sidebar.
- Closing the modal does NOT cancel the render.
- Render completes; modal shows "Open / Close".

- [ ] **Step 4: Commit**

```bash
git add server/app/static/v2/views/library.js
git commit -m "feat(ui): library modal uses poll-based progress, close is non-cancelling"
```

---

## Task 20: End-to-end smoke test (manual)

- [ ] **Step 1: Start the server locally**

```bash
cd /root/timelapse/server && uvicorn app.main:app --port 8081
```

- [ ] **Step 2: Walk the golden path**

In a browser at `http://localhost:8081`:

1. Pick a camera with frames. Go to its overview.
2. Click "Last 24 hours" → row expands. Click "MP4". Click "Render".
3. Sidebar shows `▶ Rendering · cam-X · MP4` with a progress bar that ticks up.
4. While the first job is running, queue a second one (different camera or "All time"). Sidebar shows "Queue · 1 waiting" below the running row.
5. Click ✕ on the queued row, confirm. Row disappears.
6. Wait for the running job to finish. Sidebar shows `✓ Just rendered cam-X · MP4` for ~5 s.
7. Open the renders tab — the new file is listed.
8. Open the library modal, render a different range. Close the modal mid-run; verify the sidebar still shows the job and it completes.
9. Start one more, then click ✕ on the running row, confirm. Verify partial output is removed and the job shows `⊘ Cancelled` toast.

If any step misbehaves, file the symptom and walk back through the relevant tasks.

- [ ] **Step 3: Run the full test suite**

```bash
pytest tests/server -v
```

Expected: all green (or skipped where ffmpeg is unavailable).

- [ ] **Step 4: Commit any final fixes; push branch**

```bash
git push -u origin claude/render-queue-and-server-panel
```

---

## Self-review notes (filled in during plan write-up)

- **Spec coverage:** Each spec section maps to a task: queue (1–5), ffmpeg (6–7), API contract (9–13), filename uniqueness (8), shared store (14), sidebar panel + cancel (15–17), Quick render (18), modal migration (19), e2e (20). Reaper TTL constant collision flagged in Task 5 fixed (`REAPER_INTERVAL_SECONDS` ≠ `RECENT_TTL_SECONDS`).
- **Placeholder scan:** No "TBD"/"TODO"/"add validation" patterns. Each step shows actual code or actual command + expected output.
- **Type consistency:** `JobState` field names are stable across tasks; `enqueue_with_inputs` introduced in Task 6 is the call site used in Task 10; `rendersStore` API (`getSnapshot`, `subscribe`, `cancel`) is consumed identically in Tasks 15, 17, 18, 19.
- **Note for executor:** Tasks 10 + 11 must be committed together (or 11 first) so the test added in Task 10 has the `/api/renders/{id}` endpoint to poll. The plan flags this in Task 10 step 3.
- **Reviewer-driven fixes (post-write):**
  - Task 15: `rendersStore` subscribe callback now does a surgical replace of `.server-panel` only (full sidebar rerender would have killed input focus + scroll position every 1.5 s).
  - Task 17: cancel-arming state moved to a module-level `armedCancels: Set<job_id>` so it survives panel rerenders. Per-DOM `setTimeout` would have lost the armed state on the next poll.
  - Task 19: hooks the polling loop's stop signal into the existing `openModal({ onClose })` opt (verified at `app.js:164–186`); the modal's current payload also bug-fixed from the wrong `start_at`/`end_at` keys to the server's actual `start_date`/`end_date`.
  - Task 10: clarified `selected_images` operates on YYYY-MM-DD prefixes (`main.py:884–893`); preset semantics are intentionally day-granular.
  - Task 3: `pytest-asyncio` install now points to `requirements-dev.txt` and the existing `[tool.pytest.ini_options]` block in `pyproject.toml`.
