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
