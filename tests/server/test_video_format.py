import shutil
import time
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
