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
