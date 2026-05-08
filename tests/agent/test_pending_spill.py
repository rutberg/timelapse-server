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
