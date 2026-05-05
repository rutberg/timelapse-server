import shutil
import time
from pathlib import Path
import pytest


def _seed_video(tmp_data_dir: Path, camera_id: str, name: str, size: int = 100, mtime_offset: float = 0.0) -> Path:
    video_dir = tmp_data_dir / "videos" / camera_id
    video_dir.mkdir(parents=True, exist_ok=True)
    path = video_dir / name
    path.write_bytes(b"x" * size)
    if mtime_offset:
        ts = time.time() + mtime_offset
        import os
        os.utime(path, (ts, ts))
    return path


def test_list_empty_when_no_dir(client, tmp_data_dir):
    response = client.get("/api/cameras/cam-empty/videos")
    assert response.status_code == 200
    assert response.json() == {"videos": []}


def test_list_returns_videos_sorted_newest_first(client, tmp_data_dir):
    _seed_video(tmp_data_dir, "cam-list", "old.mp4", size=10, mtime_offset=-100)
    _seed_video(tmp_data_dir, "cam-list", "new.gif", size=20, mtime_offset=0)

    body = client.get("/api/cameras/cam-list/videos").json()
    names = [v["filename"] for v in body["videos"]]
    assert names == ["new.gif", "old.mp4"]
    assert body["videos"][0]["format"] == "gif"
    assert body["videos"][1]["format"] == "mp4"
    assert body["videos"][0]["size_bytes"] == 20
    assert "created_at" in body["videos"][0]


def test_list_skips_non_video_files(client, tmp_data_dir):
    _seed_video(tmp_data_dir, "cam-mix", "real.mp4")
    (tmp_data_dir / "videos" / "cam-mix" / "leftover.txt").write_bytes(b"junk")
    (tmp_data_dir / "videos" / "cam-mix" / "palette.png").write_bytes(b"palette")

    body = client.get("/api/cameras/cam-mix/videos").json()
    names = [v["filename"] for v in body["videos"]]
    assert names == ["real.mp4"]


def test_delete_video(client, tmp_data_dir):
    path = _seed_video(tmp_data_dir, "cam-del", "doomed.mp4")
    assert path.exists()

    response = client.delete("/api/cameras/cam-del/videos/doomed.mp4")
    assert response.status_code == 204
    assert not path.exists()

    listing = client.get("/api/cameras/cam-del/videos").json()
    assert listing == {"videos": []}


def test_delete_unknown_returns_404(client, tmp_data_dir):
    response = client.delete("/api/cameras/cam-ghost/videos/nope.mp4")
    assert response.status_code == 404


def test_delete_unsupported_extension_returns_404(client, tmp_data_dir):
    (tmp_data_dir / "videos" / "cam-bad").mkdir(parents=True, exist_ok=True)
    (tmp_data_dir / "videos" / "cam-bad" / "weird.webm").write_bytes(b"x")

    response = client.delete("/api/cameras/cam-bad/videos/weird.webm")
    assert response.status_code == 404


def test_delete_validates_camera_id(client, tmp_data_dir):
    response = client.delete("/api/cameras/..bad../videos/x.mp4")
    assert response.status_code == 400
