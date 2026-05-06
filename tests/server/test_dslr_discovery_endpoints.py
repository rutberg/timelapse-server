from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient


FIXTURES = Path(__file__).parent / "fixtures" / "dslr"


@pytest.fixture
def tmp_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TIMELAPSE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv(
        "TIMELAPSE_ALLOWED_NETWORKS",
        "127.0.0.0/8,::1/128",
    )
    return tmp_path


@pytest.fixture
def client(tmp_data_dir: Path) -> Iterator[TestClient]:
    import app.main as server_main

    importlib.reload(server_main)
    with TestClient(server_main.app) as test_client:
        yield test_client


def load_raw(name: str) -> dict:
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def make_camera(client: TestClient, camera_id: str) -> None:
    """Force a camera record into existence by reading its config."""
    res = client.get(f"/api/cameras/{camera_id}/config")
    assert res.status_code == 200


class TestStartDiscovery:
    def test_returns_token_and_sets_pending_on_config(self, client: TestClient):
        make_camera(client, "cam1")
        res = client.post("/api/cameras/cam1/dslr/discovery")
        assert res.status_code == 200
        data = res.json()
        assert "token" in data and len(data["token"]) > 0
        assert "requested_at" in data

        cfg = client.get("/api/cameras/cam1/config").json()
        assert cfg["dslr_pending_discovery"]["token"] == data["token"]

    def test_each_call_mints_a_fresh_token(self, client: TestClient):
        make_camera(client, "cam1")
        t1 = client.post("/api/cameras/cam1/dslr/discovery").json()["token"]
        t2 = client.post("/api/cameras/cam1/dslr/discovery").json()["token"]
        assert t1 != t2


class TestPostDiscoveryResult:
    def test_full_round_trip_for_canon(self, client: TestClient):
        make_camera(client, "cam1")
        token = client.post("/api/cameras/cam1/dslr/discovery").json()["token"]

        raw = load_raw("canon-eos-r6.json")
        body = {"vendor": "Canon", "model": "Canon EOS R6", "serial": "012345678901"}
        res = client.post(
            "/api/cameras/cam1/dslr/discovery/result",
            json={"token": token, "body": body, "raw_config": raw},
        )
        assert res.status_code == 200
        assert res.json()["acknowledged"] is True
        assert res.json()["stale"] is False

        # Pending slot is cleared, proposal is available.
        cfg = client.get("/api/cameras/cam1/config").json()
        assert cfg["dslr_pending_discovery"] is None

        proposal = client.get("/api/cameras/cam1/dslr/discovery/proposal").json()
        assert proposal["proposal"] is not None
        assert proposal["proposal"]["body"]["vendor"] == "Canon"
        assert proposal["raw_keys"]  # at least one key
        # The full /main/imgsettings/iso path should appear in the raw_keys list.
        assert any("/imgsettings/iso" in k for k in proposal["raw_keys"])

    def test_stale_token_is_acknowledged_but_ignored(self, client: TestClient):
        make_camera(client, "cam1")
        # Don't even start discovery — agent posts a stale token.
        res = client.post(
            "/api/cameras/cam1/dslr/discovery/result",
            json={"token": "ghost", "raw_config": {}},
        )
        assert res.status_code == 200
        assert res.json()["stale"] is True

        # No proposal stored.
        proposal = client.get("/api/cameras/cam1/dslr/discovery/proposal").json()
        assert proposal["proposal"] is None

    def test_error_path_records_failure(self, client: TestClient):
        make_camera(client, "cam1")
        token = client.post("/api/cameras/cam1/dslr/discovery").json()["token"]

        res = client.post(
            "/api/cameras/cam1/dslr/discovery/result",
            json={"token": token, "raw_config": {}, "error": "no camera detected"},
        )
        assert res.status_code == 200

        proposal = client.get("/api/cameras/cam1/dslr/discovery/proposal").json()
        assert proposal["proposal"] is None
        assert proposal["discovery"]["error"] == "no camera detected"
        assert proposal["discovery"]["completed_at"] is not None


class TestPutPropertyMap:
    def test_persists_user_edited_map(self, client: TestClient):
        make_camera(client, "cam1")
        token = client.post("/api/cameras/cam1/dslr/discovery").json()["token"]
        client.post(
            "/api/cameras/cam1/dslr/discovery/result",
            json={
                "token": token,
                "raw_config": load_raw("nikon-d3400.json"),
                "body": {"vendor": "Nikon", "model": "D3400", "serial": "x"},
            },
        )
        proposal = client.get(
            "/api/cameras/cam1/dslr/discovery/proposal"
        ).json()["proposal"]

        # Simulate the user dropping the focus_mode init key.
        proposal["init_keys"] = [
            k for k in proposal["init_keys"] if k["settings_field"] != "focus_mode"
        ]
        res = client.put("/api/cameras/cam1/dslr/property_map", json=proposal)
        assert res.status_code == 200

        cfg = client.get("/api/cameras/cam1/config").json()
        assert cfg["dslr_property_map"] is not None
        kept = [k["settings_field"] for k in cfg["dslr_property_map"]["init_keys"]]
        assert "focus_mode" not in kept
        # capture_target / drive_mode survive.
        assert "capture_target" in kept
        assert "drive_mode" in kept


class TestDeletePropertyMap:
    def test_clears_map_and_proposal(self, client: TestClient):
        make_camera(client, "cam1")
        token = client.post("/api/cameras/cam1/dslr/discovery").json()["token"]
        client.post(
            "/api/cameras/cam1/dslr/discovery/result",
            json={
                "token": token,
                "raw_config": load_raw("sony-a7m4.json"),
                "body": {"vendor": "Sony", "model": "ILCE-7M4"},
            },
        )
        proposal = client.get(
            "/api/cameras/cam1/dslr/discovery/proposal"
        ).json()["proposal"]
        client.put("/api/cameras/cam1/dslr/property_map", json=proposal)

        res = client.delete("/api/cameras/cam1/dslr/property_map")
        assert res.status_code == 204

        cfg = client.get("/api/cameras/cam1/config").json()
        assert cfg["dslr_property_map"] is None

        # Discovery state is also reset so the wizard can re-run cleanly.
        proposal2 = client.get(
            "/api/cameras/cam1/dslr/discovery/proposal"
        ).json()
        assert proposal2["proposal"] is None
        assert proposal2["discovery"]["completed_at"] is None


class TestCheckinIncludesPendingDiscovery:
    def test_pending_is_visible_in_camera_config(self, client: TestClient):
        # The agent reads the camera config every poll; this is what surfaces
        # `dslr_pending_discovery` to it. We assert the round trip via GET so
        # changes to the response shape would catch a regression.
        make_camera(client, "cam1")
        client.post("/api/cameras/cam1/dslr/discovery")
        cfg = client.get("/api/cameras/cam1/config").json()
        assert cfg["dslr_pending_discovery"] is not None
        assert "token" in cfg["dslr_pending_discovery"]
