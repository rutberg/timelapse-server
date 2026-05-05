import os
import time
from pathlib import Path


def _seed_frame(
    tmp_data_dir: Path,
    camera_id: str,
    day: str,
    filename: str,
    content: bytes = b"fake-jpeg",
    mtime_offset: float = 0.0,
) -> Path:
    frame_dir = tmp_data_dir / "images" / camera_id / day
    frame_dir.mkdir(parents=True, exist_ok=True)
    path = frame_dir / filename
    path.write_bytes(content)
    if mtime_offset:
        ts = time.time() + mtime_offset
        os.utime(path, (ts, ts))
    return path


def test_list_frames_empty_when_no_images(client):
    response = client.get("/api/cameras/cam-empty/frames")
    assert response.status_code == 200
    assert response.json() == {
        "frames": [],
        "total": 0,
        "returned": 0,
        "has_more": False,
        "next_cursor": None,
    }


def test_list_frames_returns_newest_first(client, tmp_data_dir):
    _seed_frame(tmp_data_dir, "cam-frames", "2026-05-04", "20260504T143000.jpg", b"one")
    _seed_frame(tmp_data_dir, "cam-frames", "2026-05-05", "20260505T080000.jpg", b"two")

    body = client.get("/api/cameras/cam-frames/frames").json()

    assert [frame["filename"] for frame in body["frames"]] == [
        "20260505T080000.jpg",
        "20260504T143000.jpg",
    ]
    assert body["frames"][0]["day"] == "2026-05-05"
    assert body["frames"][0]["captured_at"] == "2026-05-05T08:00:00"
    assert body["frames"][0]["cursor"] == "2026-05-05/20260505T080000.jpg"
    assert body["frames"][0]["url"] == (
        "/api/cameras/cam-frames/frames/2026-05-05/20260505T080000.jpg"
    )
    assert body["frames"][0]["thumbnail_url"] == (
        "/api/cameras/cam-frames/frames/2026-05-05/20260505T080000.jpg/thumbnail"
    )
    assert body["total"] == 2
    assert body["returned"] == 2
    assert body["has_more"] is False


def test_list_frames_supports_day_filter(client, tmp_data_dir):
    _seed_frame(tmp_data_dir, "cam-days", "2026-05-04", "20260504T143000.jpg")
    _seed_frame(tmp_data_dir, "cam-days", "2026-05-05", "20260505T080000.jpg")

    body = client.get("/api/cameras/cam-days/frames?day=2026-05-04").json()

    assert body["total"] == 1
    assert [frame["filename"] for frame in body["frames"]] == ["20260504T143000.jpg"]


def test_list_frames_skips_unservable_jpegs(client, tmp_data_dir):
    _seed_frame(tmp_data_dir, "cam-mixed", "2026-05-04", "20260504T143000.jpg")
    _seed_frame(tmp_data_dir, "cam-mixed", "2026-05-04", "20260504T143100Z.jpg")
    _seed_frame(tmp_data_dir, "cam-mixed", "2026-05-04", "manual-upload.jpg")
    _seed_frame(tmp_data_dir, "cam-mixed", "not-a-day", "20260504T143100.jpg")

    body = client.get("/api/cameras/cam-mixed/frames").json()

    assert [frame["filename"] for frame in body["frames"]] == [
        "20260504T143100Z.jpg",
        "20260504T143000.jpg",
    ]
    assert body["frames"][0]["captured_at"] == "2026-05-04T14:31:00"
    assert body["total"] == 2


def test_list_frames_supports_pagination(client, tmp_data_dir):
    _seed_frame(tmp_data_dir, "cam-page", "2026-05-04", "20260504T143000.jpg")
    _seed_frame(tmp_data_dir, "cam-page", "2026-05-04", "20260504T143100.jpg")
    _seed_frame(tmp_data_dir, "cam-page", "2026-05-04", "20260504T143200.jpg")

    first = client.get("/api/cameras/cam-page/frames?limit=2").json()

    assert [frame["filename"] for frame in first["frames"]] == [
        "20260504T143200.jpg",
        "20260504T143100.jpg",
    ]
    assert first["has_more"] is True
    assert first["next_cursor"] == "2026-05-04/20260504T143100.jpg"

    second = client.get(
        "/api/cameras/cam-page/frames",
        params={"limit": 2, "before": first["next_cursor"]},
    ).json()

    assert [frame["filename"] for frame in second["frames"]] == ["20260504T143000.jpg"]
    assert second["has_more"] is False
    assert second["next_cursor"] is None


def test_list_frames_supports_ascending_order(client, tmp_data_dir):
    _seed_frame(tmp_data_dir, "cam-asc", "2026-05-04", "20260504T143000.jpg")
    _seed_frame(tmp_data_dir, "cam-asc", "2026-05-04", "20260504T143100.jpg")
    _seed_frame(tmp_data_dir, "cam-asc", "2026-05-04", "20260504T143200.jpg")

    body = client.get("/api/cameras/cam-asc/frames?day=2026-05-04&order=asc&limit=5000").json()

    assert [frame["filename"] for frame in body["frames"]] == [
        "20260504T143000.jpg",
        "20260504T143100.jpg",
        "20260504T143200.jpg",
    ]
    assert body["has_more"] is False


def test_list_frame_days_returns_month_and_day_summary(client, tmp_data_dir):
    _seed_frame(tmp_data_dir, "cam-archive", "2026-04-30", "20260430T180000.jpg", b"a")
    _seed_frame(tmp_data_dir, "cam-archive", "2026-05-04", "20260504T060000.jpg", b"bb")
    _seed_frame(tmp_data_dir, "cam-archive", "2026-05-04", "20260504T070000.jpg", b"ccc")

    body = client.get("/api/cameras/cam-archive/frame-days").json()

    assert body["total_days"] == 2
    assert body["total_frames"] == 3
    assert [day["day"] for day in body["days"]] == ["2026-05-04", "2026-04-30"]
    assert body["days"][0]["count"] == 2
    assert body["days"][0]["size_bytes"] == 5
    assert body["days"][0]["first_captured_at"] == "2026-05-04T06:00:00"
    assert body["days"][0]["last_captured_at"] == "2026-05-04T07:00:00"
    assert body["months"] == [
        {"month": "2026-05", "day_count": 1, "frame_count": 2, "gap_count": 1},
        {"month": "2026-04", "day_count": 1, "frame_count": 1, "gap_count": 0},
    ]


def test_list_frame_days_empty_when_no_images(client):
    response = client.get("/api/cameras/cam-empty/frame-days")
    assert response.status_code == 200
    assert response.json() == {
        "days": [],
        "months": [],
        "total_days": 0,
        "total_frames": 0,
    }


def test_read_frame_serves_jpeg(client, tmp_data_dir):
    _seed_frame(tmp_data_dir, "cam-read", "2026-05-04", "20260504T143000.jpg", b"jpeg-bytes")

    response = client.get("/api/cameras/cam-read/frames/2026-05-04/20260504T143000.jpg")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content == b"jpeg-bytes"


def test_read_frame_thumbnail_serves_cached_jpeg(client, tmp_data_dir):
    _seed_frame(tmp_data_dir, "cam-thumb", "2026-05-04", "20260504T143000.jpg", b"jpeg-bytes")
    thumb_dir = tmp_data_dir / "thumbnails" / "cam-thumb" / "2026-05-04"
    thumb_dir.mkdir(parents=True, exist_ok=True)
    (thumb_dir / "20260504T143000.jpg").write_bytes(b"thumb-bytes")

    response = client.get("/api/cameras/cam-thumb/frames/2026-05-04/20260504T143000.jpg/thumbnail")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content == b"thumb-bytes"


def test_read_frame_thumbnail_falls_back_to_original_without_generator(
    client,
    tmp_data_dir,
    monkeypatch,
):
    import app.main as server_main

    monkeypatch.setattr(server_main.shutil, "which", lambda _name: None)
    _seed_frame(tmp_data_dir, "cam-thumb-fallback", "2026-05-04", "20260504T143000.jpg", b"jpeg-bytes")

    response = client.get(
        "/api/cameras/cam-thumb-fallback/frames/2026-05-04/20260504T143000.jpg/thumbnail"
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content == b"jpeg-bytes"


def test_read_missing_frame_returns_404(client):
    response = client.get("/api/cameras/cam-read/frames/2026-05-04/20260504T143000.jpg")
    assert response.status_code == 404


def test_delete_frame(client, tmp_data_dir):
    path = _seed_frame(tmp_data_dir, "cam-del", "2026-05-04", "20260504T143000.jpg")
    thumb_dir = tmp_data_dir / "thumbnails" / "cam-del" / "2026-05-04"
    thumb_dir.mkdir(parents=True, exist_ok=True)
    thumb_path = thumb_dir / "20260504T143000.jpg"
    thumb_path.write_bytes(b"thumb")
    assert path.exists()
    assert thumb_path.exists()

    response = client.delete("/api/cameras/cam-del/frames/2026-05-04/20260504T143000.jpg")

    assert response.status_code == 204
    assert not path.exists()
    assert not thumb_path.exists()


def test_delete_missing_frame_returns_404(client):
    response = client.delete("/api/cameras/cam-del/frames/2026-05-04/20260504T143000.jpg")
    assert response.status_code == 404


def test_frame_endpoints_validate_day_and_filename(client):
    assert client.get("/api/cameras/cam/frames?day=../bad").status_code == 400
    assert client.get("/api/cameras/cam/frames?before=2026-05-04/not-a-frame.png").status_code == 400
    assert client.get("/api/cameras/cam/frames/not-a-day/20260504T143000.jpg").status_code == 400
    assert client.get("/api/cameras/cam/frames/2026-05-04/not-a-frame.png").status_code == 400
    assert client.delete("/api/cameras/cam/frames/2026-05-04/not-a-frame.png").status_code == 400


def test_read_legacy_z_suffix_frame(client, tmp_data_dir):
    _seed_frame(tmp_data_dir, "cam-read", "2026-05-04", "20260504T143000Z.jpg", b"jpeg")

    response = client.get("/api/cameras/cam-read/frames/2026-05-04/20260504T143000Z.jpg")

    assert response.status_code == 200
    assert response.content == b"jpeg"
