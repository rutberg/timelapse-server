# Render queue & server info panel — design

**Date:** 2026-05-07
**Branch:** `claude/render-queue-and-server-panel`
**Status:** Approved by user, pending implementation plan

## Summary

Replace the current "render runs synchronously inside the HTTP request" pipeline with a single global render queue, and expand the sidebar's bottom storage strip into a server info panel that surfaces live render activity. Wire up the dead Quick render buttons on the camera overview to a minimal MP4/GIF picker that enqueues a render at 24 fps.

## Motivation

Today `POST /api/cameras/{id}/videos` runs ffmpeg as part of the HTTP request, streaming SSE progress to that single client. Two browsers requesting renders at the same time spawn two simultaneous ffmpeg jobs; cancellation is implicit (client disconnect); there is no global view of activity. The Quick render buttons on the camera overview (`camera.js:376–382`) are rendered but unwired.

We want:

1. Centralised, observable render activity (visible everywhere, not just in the requesting tab).
2. Predictable CPU load on the appliance — at most one ffmpeg job runs at a time.
3. A working Quick render flow that confirms format before enqueuing.

## Decisions

| # | Decision | Reasoning |
|---|----------|-----------|
| D1 | Global single-job queue (max 1 concurrent ffmpeg) | Server is a small appliance; renders are CPU-bound. Concurrency cap is a single constant, raisable later. |
| D2 | Both Quick render and the existing "New render" modal go through the queue | Single source of truth; sidebar reflects all activity. |
| D3 | In-memory state only (no persistence across restart) | Restarts are rare (systemd unit, manual deploys); short renders. Avoids schema/recovery complexity. |
| D4 | Per-row abort (running and queued jobs each have their own ✕) | Matches the "see status, abort if needed" request and the panel's row-per-job layout. |
| D5 | Polling-based progress (no SSE) | Existing app is poll-based. 1.5 s during activity is plenty for renders that take ≥1 minute. |
| D6 | Quick render: row morphs in-place to MP4/GIF picker, fps fixed at 24 | Stays in the existing card pattern, no popover/modal overlay. |
| D7 | Implementation: `asyncio.Queue` + single worker task in a `RenderRunner` singleton | Native to FastAPI's loop, abort = `process.terminate()`, all observable state in one Python object. |

## Architecture

```
Frontend                                Server (FastAPI)
─────────────────────────────────       ────────────────────────────────────
Sidebar info box ─── poll ─────►        GET  /api/renders          ─┐
Quick render card ── POST ─────►        POST /api/cameras/{id}/videos │
New-render modal ──── POST ─────►       GET  /api/renders/{job_id}    ├─►  RenderRunner
                                        DELETE /api/renders/{job_id}  │     - asyncio.Queue
                                                                     ─┘     - dict[id, JobState]
                                                                            - worker task
                                                                            - current ffmpeg proc
```

### `JobState` (dataclass)

```python
@dataclass
class JobState:
    id: str                    # uuid4 hex
    camera_id: str
    format: str                # "mp4" | "gif"
    fps: int
    start_at: str | None       # ISO timestamp (resolved before enqueue)
    end_at:   str | None
    range_preset: str | None   # "24h" | "7d" | "all" | None  (display hint only)
    name: str                  # filename stem actually used
    status: str                # "queued" | "running" | "done" | "failed" | "cancelled"
    queued_at: float
    started_at: float | None
    finished_at: float | None
    total_frames: int | None
    current_frame: int | None
    percent: int | None
    eta_seconds: int | None
    error: str | None
    output_path: str | None
    cancel_requested: bool = False
```

Lifecycle: `enqueue → queued → running → (done | failed | cancelled)`. Terminal jobs linger in `jobs` for ~60 s so the sidebar can render a "Just finished" toast before they fade.

### `RenderRunner` (new module: `server/app/render_queue.py`)

```python
class RenderRunner:
    def __init__(self, data_dir: Path):
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._jobs: dict[str, JobState] = {}
        self._order: list[str] = []          # FIFO of queued ids for snapshot()
        self._worker: asyncio.Task | None = None
        self._reaper: asyncio.Task | None = None
        self._current_proc: asyncio.subprocess.Process | None = None
        self._current_id: str | None = None

    async def start(self) -> None: ...
    async def stop(self)  -> None: ...     # terminates current ffmpeg, drops queue

    def enqueue(self, job: JobState) -> int: ...
    def snapshot(self) -> dict:            # for /api/renders
        return {
            "running": <serialized current job or None>,
            "queued":  [<serialized queued jobs, FIFO>],
            "recent":  [<terminal jobs from the last 60 s>],
        }
    async def cancel(self, job_id: str) -> bool:
        # running → terminate ffmpeg, mark cancel_requested, unlink partial output
        # queued  → flag cancel_requested; worker skips when popped
        # unknown → False
```

Worker loop:

```python
async def _worker(self):
    while True:
        job_id = await self._queue.get()
        job = self._jobs[job_id]
        if job.cancel_requested:
            job.status = "cancelled"; job.finished_at = time.time()
            self._queue.task_done(); continue
        job.status = "running"; job.started_at = time.time()
        self._current_id = job_id
        try:
            await self._run_job(job)
            job.status = "cancelled" if job.cancel_requested else "done"
        except Exception as exc:
            job.status = "failed"; job.error = str(exc)
        finally:
            job.finished_at = time.time()
            self._current_proc = None
            self._current_id = None
            self._queue.task_done()
```

`_run_job` is the current `event_stream` body refactored to update `JobState` rather than yield SSE. MP4 progress comes from ffmpeg's `-progress pipe:1`. GIF reports two checkpoints (`50%` after palettegen, `100%` after paletteuse). ETA is `(total - current) / observed_fps`, smoothed and clamped, `None` until enough samples exist.

A reaper task scans `_jobs` every 5 s and evicts terminal entries older than 60 s, also pruning `_order`.

## API changes (in `server/app/main.py`)

| Method   | Path                                    | Behaviour |
| -------- | --------------------------------------- | --------- |
| `POST`   | `/api/cameras/{camera_id}/videos`       | Validate request; resolve `range_preset` (if present) into `start_at`/`end_at`; run `selected_images()` upfront so empty selections still 404 synchronously; create `JobState`; `runner.enqueue()`; return **HTTP 202** with `{job_id, status: "queued", position}`. No SSE. |
| `GET`    | `/api/renders`                          | Return `runner.snapshot()`. |
| `GET`    | `/api/renders/{job_id}`                 | Return single serialized `JobState`. 404 when unknown. |
| `DELETE` | `/api/renders/{job_id}`                 | `runner.cancel()` → 204 on success, 404 when unknown. Idempotent: cancelling an already-terminal job returns 204. |
| `GET`    | `/api/cameras/{camera_id}/videos`       | Unchanged — lists completed files on disk. |

`VideoRequest` gains an optional `range_preset: Literal["24h","7d","all"] | None`. When present, the server overrides `start_at`/`end_at`; when absent, behaviour is unchanged.

`POST` no longer returns a `StreamingResponse`. Existing tests (`tests/server/test_video_format.py`, `tests/server/test_videos_listing.py`) consume the old SSE shape and must be updated to assert the new 202/job_id flow and poll `GET /api/renders/{job_id}` until terminal.

Startup wiring: the FastAPI `lifespan` (or `@app.on_event("startup")` if that pattern is what the repo uses) constructs `RenderRunner(DATA_DIR)`, starts it, and exposes it via a module-level singleton. Shutdown calls `runner.stop()`.

## Frontend changes

### Sidebar info box (`server/app/static/v2/components/sidebar.js`, `styles.css`)

Replaces the existing 70 px storage strip with a `<section class="server-panel">`. Always shows storage; render rows appear conditionally.

```
┌─ SERVER ────────────────────────────────┐
│ ⛁  Storage                              │
│    1.4 TB / 2.0 TB                      │
│    ▰▰▰▰▰▰▰▰▱▱▱▱▱▱   70%                 │
│                                         │
│ ▶  Rendering · cam-frontporch · MP4     │
│    ▰▰▰▰▰▰▱▱▱▱▱▱▱▱  42% · ~01:13 left   │
│    1,260 / 3,000 frames          ✕      │
│                                         │
│ ⌛ Queue · 2 waiting             ▾      │
│    cam-backyard  · GIF  · 7d     ✕      │
│    cam-driveway  · MP4  · 24h    ✕      │
│                                         │
│ ✓ Just rendered cam-attic · MP4         │
└─────────────────────────────────────────┘
```

Rules:

- **Idle** (no running, empty queue, no recent): only Storage row visible. Panel collapses to roughly today's height.
- **Running**: live progress bar, percent, ETA, frame counter, per-row ✕.
- **Queue rows**: one per queued job in FIFO order. Each shows `camera · format · range-label`. Per-row ✕. Header collapsible (default expanded when ≥ 1).
- **Recent toast**: most recent terminal job from `recent` shows for 5 s with status icon (`✓`, `✕`, `⊘`). Failed jobs include a tooltip with `error`. Click-to-dismiss-early.
- **Cancel UX**: ✕ click shows armed `Cancel?` for ~1 s; second click commits. Running cancel adds `(cancelling…)` until snapshot reports `cancelled`.
- **Polling**: 1.5 s while there's a running or queued job; 10 s while idle. Pauses while `document.visibilityState !== "visible"`, and fires an immediate refresh when the tab becomes visible again.
- **Range labels**: client uses `range_preset` when set ("24h" / "7d" / "all"), else formats the date range from `start_at`/`end_at`.

Storage continues to come from the existing `/api/cameras` `stats` payload — no new endpoint.

### Quick render card (`server/app/static/v2/views/camera.js` `renderOverview`)

Replaces the dead button block at lines 376–382.

Default — three rows:

```
Quick render
┌──────────────────────┐
│ Last 24 hours      → │
│ Last 7 days        → │
│ All time           → │
└──────────────────────┘
```

Click "Last 24 hours" → that row morphs in-place:

```
│ Last 24 hours                    │
│   [ MP4 ] [ GIF ]    [ Render ]  │   ← MP4 default
```

Behaviour:

- Only one row expanded at a time. Clicking another range collapses the current and expands the new one. Clicking the row label again or the range button while expanded collapses it.
- Format toggle uses the existing `.seg` style.
- "Render" `POST`s to `/api/cameras/{id}/videos` with `{format, fps: 24, range_preset, name: null}`.
- After 202, the row briefly flashes "Queued" then collapses. No inline progress on the card — progress lives in the sidebar.
- If a Quick render for the same `camera + range_preset + format` is already queued or running, "Render" shows "Already queued" disabled state. Cheap dedupe: the camera overview polls `/api/renders` on the same 1.5 s / 10 s cadence the sidebar uses, sharing the cached snapshot via a small module in `app.js`. The full modal is unaffected.

### "New render" modal (`server/app/static/v2/views/library.js`)

The modal is shared between camera and library pages and currently consumes the SSE stream. Migration:

1. POST returns `{job_id}` (no longer a stream).
2. Modal switches to its existing "rendering" state, but the progress bar/ETA/frame counter are populated by polling `/api/renders/{job_id}` every 1.5 s.
3. Terminal states:
   - `done` → "Open" / "Close" buttons (Open links to the new file).
   - `failed` → error message + "Try again" (re-POSTs the same `VideoRequest`, getting a fresh `job_id`).
   - `cancelled` → close.
4. Modal close while a job is running does **not** cancel the job. The modal shows: *"You can close this — the render will keep going. Manage it from the sidebar."*

Modal keeps fps + format + date-range pickers. No `range_preset` field — that's a Quick-render shortcut only.

## Data flow examples

### Quick render — Last 7 days, MP4

1. User clicks `Last 7 days`, picks MP4, clicks `Render`.
2. Browser POSTs `{format: "mp4", fps: 24, range_preset: "7d"}` to `/api/cameras/cam-backyard/videos`.
3. Server resolves `start_at`/`end_at`, runs `selected_images()` (returns 404 if empty), creates `JobState`, calls `runner.enqueue()`, returns 202 + `{job_id, status: "queued", position: 1}`.
4. Sidebar's next `/api/renders` poll surfaces a `running` (or `queued`) row.
5. Worker reaches the job, spawns ffmpeg, updates `current_frame` and `percent` from `-progress pipe:1`.
6. Sidebar progress bar updates each poll. Modal (if open from a different camera) ignores this job.
7. Job ends; sidebar shows recent toast for 5 s; reaper evicts the entry after 60 s.

### Cancel a queued job

1. Sidebar shows two queued rows. User clicks ✕ on the second.
2. First click arms `Cancel?`; second click within ~1 s sends `DELETE /api/renders/{job_id}`.
3. Server: `runner.cancel()` flags `cancel_requested = True`; the row disappears on the next snapshot poll. Worker, when it pops the cancelled id, sees the flag and skips.

### Cancel the running job

1. User clicks ✕ on the running row, confirms.
2. `DELETE /api/renders/{job_id}` → `runner.cancel()` calls `self._current_proc.terminate()`, sets `cancel_requested`, unlinks the partial output and the `.txt` concat list.
3. Worker's `_run_job` raises (process killed); the `finally` block sees `cancel_requested` and marks the job `cancelled`.
4. Sidebar shows `⊘ Cancelled` toast.

## Filename uniqueness

The existing code generates `timelapse-<YYYYMMDDTHHMMSSZ>` when no `name` is supplied; two requests within the same second would collide. With a queue, two POSTs in the same second are easy to imagine. The runner appends a short suffix from `job_id` (first 6 chars) when no explicit name is provided: `timelapse-<timestamp>-<job6>`. Explicit names are passed through unchanged; collisions there are the user's choice and overwrite as today.

## Edge cases & non-goals

- **Server shutdown while running**: `runner.stop()` terminates ffmpeg, removes partial output and concat list, drops queued jobs.
- **Disk full / ffmpeg failure**: job ends `failed` with captured stderr in `error`. Sidebar tooltip surfaces it. No automatic retry.
- **Multiple browser tabs**: all poll independently; snapshots are consistent. Cancels are idempotent.
- **Concurrency cap**: hardcoded to 1 in `render_queue.py` as `MAX_CONCURRENT_RENDERS`. Documented as the single change point if a future concurrency cap is desired. Out of scope now.
- **Out of scope**: persistence across restart, per-camera throttling, render priorities, scheduling/queueing for off-hours.

## Testing

- **Unit (`tests/server/test_render_queue.py`, new):**
  - Enqueue → snapshot reflects FIFO position.
  - Cancel queued job → flagged, snapshot drops it after worker pops.
  - Cancel running job → `terminate()` called, partial output unlinked, status `cancelled`.
  - Reaper evicts terminal entries older than 60 s.
  - `range_preset` resolution sets `start_at`/`end_at` correctly.
- **Integration (updates to `tests/server/test_video_format.py`, `tests/server/test_videos_listing.py`):**
  - POST returns 202 + `job_id`.
  - Polling `/api/renders/{job_id}` yields a `done` state with `output_path`.
  - File at `output_path` exists and is the expected format.
- **Manual smoke**:
  - Quick render row morph + Render → sidebar shows progress → file appears in renders tab.
  - Two parallel POSTs → second job sits queued until first completes.
  - Cancel running → partial file removed.
  - Cancel queued → row disappears, currently-running job continues unaffected.

## Implementation notes

- New file: `server/app/render_queue.py` (RenderRunner + JobState).
- Modify: `server/app/main.py` (endpoints + lifespan wiring).
- Modify: `server/app/static/v2/components/sidebar.js`, `styles.css` (server panel).
- Modify: `server/app/static/v2/views/camera.js` (Quick render expansion + wiring).
- Modify: `server/app/static/v2/views/library.js` (modal poll-based progress).
- Modify: `tests/server/test_video_format.py`, `tests/server/test_videos_listing.py` (new POST contract).
- New: `tests/server/test_render_queue.py`.
