import importlib

import pytest


@pytest.fixture
def main_module(monkeypatch, tmp_path):
    monkeypatch.setenv("TIMELAPSE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TIMELAPSE_ALLOWED_NETWORKS", "127.0.0.0/8")
    import app.main as main
    importlib.reload(main)
    return main


def test_loopback_url_substituted_with_lan_ip(monkeypatch, main_module):
    monkeypatch.setattr(main_module, "detect_lan_ip", lambda: "10.0.0.5")
    monkeypatch.delenv("TIMELAPSE_PUBLIC_URL", raising=False)
    assert main_module.resolve_public_server_url("http://127.0.0.1:8080") == "http://10.0.0.5:8080"
    assert main_module.resolve_public_server_url("http://127.0.0.1") == "http://10.0.0.5"
    assert main_module.resolve_public_server_url("http://localhost:8080") == "http://10.0.0.5:8080"


def test_non_loopback_url_passes_through(monkeypatch, main_module):
    monkeypatch.setattr(main_module, "detect_lan_ip", lambda: "10.0.0.5")
    monkeypatch.delenv("TIMELAPSE_PUBLIC_URL", raising=False)
    assert (
        main_module.resolve_public_server_url("http://192.168.1.10:8080")
        == "http://192.168.1.10:8080"
    )
    assert main_module.resolve_public_server_url("http://timelapse.lan") == "http://timelapse.lan"


def test_env_override_wins_over_loopback(monkeypatch, main_module):
    monkeypatch.setenv("TIMELAPSE_PUBLIC_URL", "http://timelapse.lan:7000")
    assert (
        main_module.resolve_public_server_url("http://127.0.0.1:8080")
        == "http://timelapse.lan:7000"
    )


def test_env_override_strips_trailing_slash(monkeypatch, main_module):
    monkeypatch.setenv("TIMELAPSE_PUBLIC_URL", "http://timelapse.lan:7000/")
    assert (
        main_module.resolve_public_server_url("http://127.0.0.1:8080")
        == "http://timelapse.lan:7000"
    )


def test_loopback_url_unchanged_when_lan_undetectable(monkeypatch, main_module):
    monkeypatch.setattr(main_module, "detect_lan_ip", lambda: None)
    monkeypatch.delenv("TIMELAPSE_PUBLIC_URL", raising=False)
    assert (
        main_module.resolve_public_server_url("http://127.0.0.1:8080")
        == "http://127.0.0.1:8080"
    )
