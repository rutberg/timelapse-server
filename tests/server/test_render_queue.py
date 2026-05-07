import asyncio
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
