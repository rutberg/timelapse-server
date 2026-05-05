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


def test_delete_removes_thumbnail_directory(client, tmp_data_dir):
    thumb_dir = tmp_data_dir / "thumbnails" / "cam-thumbs" / "2026-05-04"
    thumb_dir.mkdir(parents=True, exist_ok=True)
    (thumb_dir / "1.jpg").write_bytes(b"thumb")

    response = client.delete("/api/cameras/cam-thumbs")

    assert response.status_code == 204
    assert not (tmp_data_dir / "thumbnails" / "cam-thumbs").exists()


def test_delete_unknown_camera_returns_204(client, tmp_data_dir):
    """Idempotent: deleting a never-existed camera is a no-op success."""
    response = client.delete("/api/cameras/cam-ghost")
    assert response.status_code == 204


def test_delete_validates_camera_id(client, tmp_data_dir):
    response = client.delete("/api/cameras/..bad..")
    assert response.status_code == 400


def test_delete_removes_agent_manifest(client, tmp_data_dir):
    # Create a pending agent (writes the manifest on disk).
    client.post("/api/agents", json={
        "agent_id": "cam-keys",
        "display_name": "Cam keys",
        "expected_hostname": "cam-keys",
        "ssh_user": "pi",
    })

    response = client.delete("/api/cameras/cam-keys")
    assert response.status_code == 204

    # Manifest should be gone — listing should not include the agent.
    listing = client.get("/api/agents").json()
    ids = [a["agent_id"] for a in listing.get("agents", [])]
    assert "cam-keys" not in ids


def test_delete_removes_agent_key_directory(client, tmp_data_dir):
    # Seed a fake key directory.
    key_dir = tmp_data_dir / "agents" / "cam-keys2"
    key_dir.mkdir(parents=True, exist_ok=True)
    (key_dir / "id_ed25519").write_bytes(b"PRIVATE")
    (key_dir / "id_ed25519.pub").write_text("ssh-ed25519 AAAA test")

    response = client.delete("/api/cameras/cam-keys2")
    assert response.status_code == 204
    assert not (tmp_data_dir / "agents" / "cam-keys2").exists()
