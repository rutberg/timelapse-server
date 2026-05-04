def test_delete_removes_camera_record(client, tmp_data_dir):
    client.post("/api/cameras/cam-doomed/checkin", json={"agent_version": "0.8.0"})

    response = client.delete("/api/cameras/cam-doomed")
    assert response.status_code == 204

    listing = client.get("/api/cameras").json()
    ids = [c["camera_id"] for c in listing["cameras"]]
    assert "cam-doomed" not in ids


def test_delete_removes_image_directory(client, tmp_data_dir):
    img_dir = tmp_data_dir / "images" / "cam-imgs" / "2026-05-04"
    img_dir.mkdir(parents=True, exist_ok=True)
    (img_dir / "1.jpg").write_bytes(b"junk")

    response = client.delete("/api/cameras/cam-imgs")
    assert response.status_code == 204
    assert not (tmp_data_dir / "images" / "cam-imgs").exists()


def test_delete_unknown_camera_returns_204(client, tmp_data_dir):
    """Idempotent: deleting a never-existed camera is a no-op success."""
    response = client.delete("/api/cameras/cam-ghost")
    assert response.status_code == 204


def test_delete_validates_camera_id(client, tmp_data_dir):
    response = client.delete("/api/cameras/..bad..")
    assert response.status_code == 400
