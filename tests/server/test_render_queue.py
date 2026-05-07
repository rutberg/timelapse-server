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
