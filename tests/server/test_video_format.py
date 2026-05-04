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
