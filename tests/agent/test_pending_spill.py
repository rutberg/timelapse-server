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
