import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


def test_camera_marked_offline_when_last_seen_old(tmp_data_dir, client):
    client.post(
        "/api/cameras/tomatoes/checkin",
        json={"agent_version": "0.3.0"},
    )

    raw = json.loads((tmp_data_dir / "config.json").read_text(encoding="utf-8"))
    old = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat().replace("+00:00", "Z")
    raw["cameras"]["tomatoes"]["status"]["last_seen"] = old
    (tmp_data_dir / "config.json").write_text(json.dumps(raw), encoding="utf-8")

    record = next(
        c for c in client.get("/api/cameras").json()["cameras"]
        if c["camera_id"] == "tomatoes"
    )
    assert record["status"]["is_online"] is False


def test_camera_marked_online_when_last_seen_recent(client):
    client.post("/api/cameras/tomatoes/checkin", json={"agent_version": "0.3.0"})
    record = next(
        c for c in client.get("/api/cameras").json()["cameras"]
        if c["camera_id"] == "tomatoes"
    )
    assert record["status"]["is_online"] is True


def test_camera_offline_when_never_seen(client):
    client.put(
        "/api/cameras/tomatoes/config",
        json={
            "enabled": True,
            "interval_seconds": 600,
            "image_width": None,
            "image_height": None,
            "jpeg_quality": 85,
        },
    )
    record = next(
        c for c in client.get("/api/cameras").json()["cameras"]
        if c["camera_id"] == "tomatoes"
    )
    assert record["status"]["is_online"] is False
