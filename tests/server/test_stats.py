import os
from pathlib import Path


def test_cameras_listing_includes_stats(client, tmp_data_dir):
    # Seed a camera so the listing isn't empty.
    client.post("/api/cameras/cam-stats/checkin", json={"agent_version": "0.8.0"})

    response = client.get("/api/cameras")
    assert response.status_code == 200
    body = response.json()

    assert "stats" in body
    stats = body["stats"]
    assert "storage_bytes" in stats
    assert "storage_capacity_bytes" in stats
    assert isinstance(stats["storage_bytes"], int)
    assert isinstance(stats["storage_capacity_bytes"], int)
    assert stats["storage_capacity_bytes"] >= stats["storage_bytes"]


def test_storage_bytes_counts_uploads(client, tmp_data_dir):
    # A small file written into the images tree should be reflected.
    images_dir = Path(tmp_data_dir) / "images" / "cam-bytes" / "2026-05-04"
    images_dir.mkdir(parents=True, exist_ok=True)
    (images_dir / "1234.jpg").write_bytes(b"x" * 1000)

    body = client.get("/api/cameras").json()
    assert body["stats"]["storage_bytes"] >= 1000
