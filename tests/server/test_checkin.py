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


def test_checkin_records_pending_queue_metrics(client):
    response = client.post(
        "/api/cameras/tomatoes/checkin",
        json={
            "agent_version": "0.3.0",
            "pending_count": 7,
            "pending_bytes": 2_500_000,
        },
    )
    assert response.status_code == 200

    record = next(
        c for c in client.get("/api/cameras").json()["cameras"]
        if c["camera_id"] == "tomatoes"
    )
    assert record["status"]["pending_count"] == 7
    assert record["status"]["pending_bytes"] == 2_500_000


def test_checkin_records_schedule_state(client):
    response = client.post(
        "/api/cameras/tomatoes/checkin",
        json={
            "agent_version": "0.6.0",
            "in_schedule": False,
            "local_hour": 3,
        },
    )
    assert response.status_code == 200

    record = next(
        c for c in client.get("/api/cameras").json()["cameras"]
        if c["camera_id"] == "tomatoes"
    )
    assert record["status"]["in_schedule"] is False
    assert record["status"]["local_hour"] == 3


def test_checkin_local_hour_out_of_range_rejected(client):
    response = client.post(
        "/api/cameras/tomatoes/checkin",
        json={"agent_version": "0.6.0", "local_hour": 24},
    )
    assert response.status_code == 422


def test_checkin_pending_count_defaults_to_zero(client):
    client.post(
        "/api/cameras/tomatoes/checkin",
        json={"agent_version": "0.3.0"},
    )
    record = next(
        c for c in client.get("/api/cameras").json()["cameras"]
        if c["camera_id"] == "tomatoes"
    )
    assert record["status"]["pending_count"] == 0
    assert record["status"]["pending_bytes"] == 0


def test_checkin_creates_camera_if_missing(client):
    response = client.post(
        "/api/cameras/new-camera/checkin",
        json={"agent_version": "0.3.1"},
    )
    assert response.status_code == 200
    cameras = client.get("/api/cameras").json()["cameras"]
    assert any(c["camera_id"] == "new-camera" for c in cameras)


def test_checkin_accepts_current_light(client, tmp_data_dir):
    response = client.post(
        "/api/cameras/cam-light/checkin",
        json={"agent_version": "0.8.0", "current_light": 142},
    )
    assert response.status_code == 200

    listing = client.get("/api/cameras").json()
    cam = next(c for c in listing["cameras"] if c["camera_id"] == "cam-light")
    assert cam["status"]["current_light"] == 142


def test_checkin_accepts_signal_dbm(client, tmp_data_dir):
    response = client.post(
        "/api/cameras/cam-rssi/checkin",
        json={"agent_version": "0.8.0", "signal_dbm": -62},
    )
    assert response.status_code == 200

    listing = client.get("/api/cameras").json()
    cam = next(c for c in listing["cameras"] if c["camera_id"] == "cam-rssi")
    assert cam["status"]["signal_dbm"] == -62


def test_checkin_current_light_range(client, tmp_data_dir):
    bad = client.post(
        "/api/cameras/cam-bad/checkin",
        json={"agent_version": "0.8.0", "current_light": 300},
    )
    assert bad.status_code == 422
