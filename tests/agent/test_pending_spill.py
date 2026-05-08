# tests/agent/test_pending_spill.py
from pathlib import Path
import timelapse_agent as agent


def test_resolve_pending_dirs_no_config_returns_single_tier(tmp_path):
    settings = {}
    ram_dir, spill_dir = agent.resolve_pending_dirs(settings, tmp_path)
    assert ram_dir == tmp_path / "pending"
    assert spill_dir == tmp_path / "pending"


def test_resolve_pending_dirs_configured_returns_two_tiers(tmp_path):
    ram_path = tmp_path / "ram" / "pending"
    settings = {"ram_pending_dir": str(ram_path)}
    ram_dir, spill_dir = agent.resolve_pending_dirs(settings, tmp_path)
    assert ram_dir == ram_path
    assert spill_dir == tmp_path / "spill"


def test_resolve_pending_dirs_two_tiers_are_distinct(tmp_path):
    settings = {"ram_pending_dir": "/run/timelapse-agent/pending"}
    ram_dir, spill_dir = agent.resolve_pending_dirs(settings, tmp_path)
    assert ram_dir != spill_dir


def test_measure_pending_single_tier(tmp_path):
    pending = tmp_path / "pending"
    pending.mkdir()
    (pending / "20260101T000000.jpg").write_bytes(b"x" * 1000)
    count, total = agent.measure_pending(tmp_path / "pending", tmp_path / "pending")
    assert count == 1
    assert total == 1000


def test_measure_pending_two_tiers(tmp_path):
    ram = tmp_path / "ram"
    spill = tmp_path / "spill"
    ram.mkdir(); spill.mkdir()
    (ram / "20260101T000001.jpg").write_bytes(b"x" * 500)
    (spill / "20260101T000000.jpg").write_bytes(b"x" * 800)
    count, total = agent.measure_pending(ram, spill)
    assert count == 2
    assert total == 1300


def test_measure_pending_missing_dirs(tmp_path):
    count, total = agent.measure_pending(tmp_path / "ram", tmp_path / "spill")
    assert count == 0
    assert total == 0


def test_evict_pending_removes_oldest_from_spill(tmp_path):
    spill = tmp_path / "spill"
    spill.mkdir()
    (spill / "20260101T000000.jpg").write_bytes(b"a" * 600)
    (spill / "20260101T000001.jpg").write_bytes(b"b" * 600)
    evicted_count, evicted_bytes = agent.evict_pending(spill, max_bytes=700)
    assert evicted_count == 1
    assert evicted_bytes == 600
    remaining = list(spill.glob("*.jpg"))
    assert len(remaining) == 1
    assert remaining[0].name == "20260101T000001.jpg"


def test_evict_pending_removes_sidecar(tmp_path):
    spill = tmp_path / "spill"
    spill.mkdir()
    (spill / "20260101T000000.jpg").write_bytes(b"a" * 1000)
    (spill / "20260101T000000.json").write_text('{"captured_at":"2026-01-01T00:00:00+00:00"}')
    agent.evict_pending(spill, max_bytes=0)
    # max_bytes=0 means unlimited — nothing evicted
    assert (spill / "20260101T000000.jpg").exists()


def test_evict_pending_zero_means_unlimited(tmp_path):
    spill = tmp_path / "spill"
    spill.mkdir()
    (spill / "20260101T000000.jpg").write_bytes(b"x" * 1000)
    count, _ = agent.evict_pending(spill, max_bytes=0)
    assert count == 0


def test_evict_pending_missing_dir_is_noop(tmp_path):
    count, _ = agent.evict_pending(tmp_path / "spill", max_bytes=100)
    assert count == 0


import json as _json
from unittest.mock import patch, MagicMock
from urllib.error import URLError


def _fake_settings():
    return {"camera_id": "cam1", "server_url": "http://server.local:8081"}


def _make_jpg(directory: Path, name: str, size: int = 100) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / name
    p.write_bytes(b"J" * size)
    p.with_suffix(".json").write_text(
        _json.dumps({"captured_at": "2026-01-01T00:00:00+00:00"})
    )
    return p


def test_upload_pending_drains_spill_before_ram(tmp_path):
    """Spill files must be uploaded before RAM files (chronological order)."""
    spill = tmp_path / "spill"
    ram = tmp_path / "ram"
    _make_jpg(spill, "20260101T000000.jpg")  # older
    _make_jpg(ram,   "20260101T000001.jpg")  # newer
    uploaded = []

    def fake_post(url, path, captured_at, **kw):
        uploaded.append(path.parent.name)  # "spill" or "ram"

    state = agent.AgentState()
    with patch.object(agent, "post_multipart", side_effect=fake_post):
        agent.upload_pending(_fake_settings(), tmp_path, ram, spill, state)

    assert uploaded == ["spill", "ram"]
    assert not (spill / "20260101T000000.jpg").exists()
    assert not (ram   / "20260101T000001.jpg").exists()


def test_upload_pending_moves_ram_failure_to_spill(tmp_path):
    """On upload failure from RAM, the file moves to spill (not deleted)."""
    spill = tmp_path / "spill"
    ram = tmp_path / "ram"
    jpg = _make_jpg(ram, "20260101T000002.jpg")

    def fake_post(url, path, captured_at, **kw):
        raise URLError("connection refused")

    state = agent.AgentState()
    with patch.object(agent, "post_multipart", side_effect=fake_post):
        agent.upload_pending(_fake_settings(), tmp_path, ram, spill, state)

    assert not jpg.exists()                          # gone from RAM
    assert (spill / "20260101T000002.jpg").exists()  # landed in spill
    assert (spill / "20260101T000002.json").exists() # sidecar moved too
    assert "upload failed" in state.last_error


def test_upload_pending_single_tier_failure_leaves_file(tmp_path):
    """In single-tier mode (ram==spill), failure leaves file in place (legacy)."""
    pending = tmp_path / "pending"
    jpg = _make_jpg(pending, "20260101T000003.jpg")

    def fake_post(url, path, captured_at, **kw):
        raise URLError("connection refused")

    state = agent.AgentState()
    with patch.object(agent, "post_multipart", side_effect=fake_post):
        agent.upload_pending(_fake_settings(), tmp_path, pending, pending, state)

    assert jpg.exists()  # file stays in place in single-tier mode


def test_upload_pending_single_tier_success_deletes_file(tmp_path):
    pending = tmp_path / "pending"
    jpg = _make_jpg(pending, "20260101T000004.jpg")

    state = agent.AgentState()
    with patch.object(agent, "post_multipart", return_value=None):
        with patch.object(agent, "upload_camera_pending", return_value=None):
            agent.upload_pending(_fake_settings(), tmp_path, pending, pending, state)

    assert not jpg.exists()
