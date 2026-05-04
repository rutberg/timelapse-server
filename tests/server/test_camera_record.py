import json
from pathlib import Path


def write_legacy_store(data_dir: Path) -> None:
    legacy = {
        "cameras": {
            "tomatoes": {
                "enabled": True,
                "interval_seconds": 600,
                "image_width": 1920,
                "image_height": 1080,
                "jpeg_quality": 90,
                "config_version": 4,
            }
        }
    }
    (data_dir / "config.json").write_text(json.dumps(legacy), encoding="utf-8")


def test_legacy_store_is_migrated_on_read(tmp_data_dir, client):
    write_legacy_store(tmp_data_dir)

    response = client.get("/api/cameras/tomatoes/config")

    assert response.status_code == 200
    assert response.json()["interval_seconds"] == 600
    assert response.json()["image_width"] == 1920


def test_status_block_exists_after_migration(tmp_data_dir, client):
    write_legacy_store(tmp_data_dir)

    client.get("/api/cameras/tomatoes/config")
    raw = json.loads((tmp_data_dir / "config.json").read_text(encoding="utf-8"))

    record = raw["cameras"]["tomatoes"]
    assert "config" in record
    assert "status" in record
    assert record["status"]["last_seen"] is None
    assert record["status"]["agent_version"] is None
