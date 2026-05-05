import json
import shutil
from pathlib import Path
import pytest


@pytest.fixture(autouse=True)
def _seed_two_jpegs(tmp_data_dir):
    """Drop two minimal JPEG files into the images tree so ffmpeg has frames."""
    cam_dir = Path(tmp_data_dir) / "images" / "cam-vids" / "2026-05-04"
    cam_dir.mkdir(parents=True, exist_ok=True)
    # Minimal valid 2x2 JPEG (dimensions divisible by 2 as required by libx264).
    # Generated via: ffmpeg -f lavfi -i color=c=black:size=2x2:duration=0.1 -frames:v 1 out.jpg
    # The spec's original hex was malformed (truncated Huffman tables); this replaces it.
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


def parse_sse_done(response) -> dict:
    """Parse an SSE response and return the data from the 'done' event.

    Raises AssertionError if an 'error' event is received.
    """
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    done_data = None
    for raw_event in response.text.split("\n\n"):
        lines = raw_event.strip().split("\n")
        event_type = next(
            (l.split(":", 1)[1].strip() for l in lines if l.startswith("event:")), None
        )
        data_str = next(
            (l.split(":", 1)[1].strip() for l in lines if l.startswith("data:")), None
        )
        if not event_type or not data_str:
            continue
        data = json.loads(data_str)
        if event_type == "error":
            raise AssertionError(f"SSE error event: {data.get('detail')}")
        if event_type == "done":
            done_data = data
    assert done_data is not None, "No 'done' SSE event received"
    return done_data


def test_default_format_is_mp4(client):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    response = client.post(
        "/api/cameras/cam-vids/videos",
        json={"start_date": "2026-05-04", "end_date": "2026-05-04", "fps": 12},
    )
    done = parse_sse_done(response)
    assert done["path"].endswith(".mp4")


def test_explicit_mp4_format(client):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    response = client.post(
        "/api/cameras/cam-vids/videos",
        json={"start_date": "2026-05-04", "end_date": "2026-05-04", "fps": 12, "format": "mp4"},
    )
    done = parse_sse_done(response)
    assert done["path"].endswith(".mp4")


def test_gif_format(client):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    response = client.post(
        "/api/cameras/cam-vids/videos",
        json={"start_date": "2026-05-04", "end_date": "2026-05-04", "fps": 12, "format": "gif"},
    )
    done = parse_sse_done(response)
    assert done["path"].endswith(".gif")


def test_invalid_format_rejected(client):
    response = client.post(
        "/api/cameras/cam-vids/videos",
        json={"start_date": "2026-05-04", "end_date": "2026-05-04", "fps": 12, "format": "webm"},
    )
    assert response.status_code == 422


def test_gif_can_be_downloaded(client):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    done = parse_sse_done(client.post(
        "/api/cameras/cam-vids/videos",
        json={"start_date": "2026-05-04", "end_date": "2026-05-04", "fps": 12, "format": "gif"},
    ))
    filename = done["path"].split("/")[-1]
    response = client.get(f"/api/cameras/cam-vids/videos/{filename}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/gif"


def test_mp4_can_be_downloaded(client):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    done = parse_sse_done(client.post(
        "/api/cameras/cam-vids/videos",
        json={"start_date": "2026-05-04", "end_date": "2026-05-04", "fps": 12, "format": "mp4"},
    ))
    filename = done["path"].split("/")[-1]
    response = client.get(f"/api/cameras/cam-vids/videos/{filename}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "video/mp4"


def test_unsupported_extension_returns_404(client):
    response = client.get("/api/cameras/cam-vids/videos/foo.webm")
    assert response.status_code == 404
