def test_checkin_records_status_fields(client):
    response = client.post(
        "/api/cameras/tomatoes/checkin",
        json={
            "agent_version": "0.3.1",
            "hostname": "timelapse-tomatoes",
            "last_capture_at": "2026-05-03T12:00:00Z",
            "last_upload_at": "2026-05-03T12:00:05Z",
            "last_error": None,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["acknowledged"] is True

    detail = client.get("/api/cameras").json()
    record = next(c for c in detail["cameras"] if c["camera_id"] == "tomatoes")
    assert record["status"]["agent_version"] == "0.3.1"
    assert record["status"]["hostname"] == "timelapse-tomatoes"
    assert record["status"]["last_seen"] is not None
    assert record["status"]["source_ip"] == "testclient"


def test_checkin_with_error_persists_error(client):
    client.post(
        "/api/cameras/tomatoes/checkin",
        json={"agent_version": "0.3.1", "last_error": "camera not detected"},
    )
    record = next(
        c for c in client.get("/api/cameras").json()["cameras"]
        if c["camera_id"] == "tomatoes"
    )
    assert record["status"]["last_error"] == "camera not detected"


def test_checkin_creates_camera_if_missing(client):
    response = client.post(
        "/api/cameras/new-camera/checkin",
        json={"agent_version": "0.3.1"},
    )
    assert response.status_code == 200
    cameras = client.get("/api/cameras").json()["cameras"]
    assert any(c["camera_id"] == "new-camera" for c in cameras)
