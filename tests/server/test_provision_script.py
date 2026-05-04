from app.provision_script import build_install_script


def test_script_contains_camera_id_in_config():
    script = build_install_script(
        camera_id="tomatoes",
        server_url="http://192.168.1.10:8080",
        agent_version="0.3.0",
    )
    assert '"camera_id": "tomatoes"' in script
    assert "http://192.168.1.10:8080" in script


def test_script_uses_set_e_and_pipefail():
    script = build_install_script(
        camera_id="x",
        server_url="http://x:8080",
        agent_version="0.3.0",
    )
    assert script.startswith("#!/usr/bin/env bash\nset -euo pipefail\n")


def test_script_installs_camera_packages_and_enables_service():
    script = build_install_script(
        camera_id="x",
        server_url="http://x:8080",
        agent_version="0.3.0",
    )
    assert "rpicam-apps-lite" in script
    assert "systemctl daemon-reload" in script
    assert "systemctl enable --now timelapse-agent" in script


def test_script_writes_version_file_into_version_dir():
    script = build_install_script(
        camera_id="x",
        server_url="http://x:8080",
        agent_version="0.3.0",
    )
    assert "AGENT_VERSION=\"0.3.0\"" in script
    assert "sudo tee \"$VERSION_DIR/VERSION\"" in script
    assert "sudo chmod 644 \"$VERSION_DIR/VERSION\"" in script


def test_script_rejects_dangerous_input():
    import pytest
    with pytest.raises(ValueError):
        build_install_script(
            camera_id="ok",
            server_url="http://ok:8080;rm -rf /",
            agent_version="0.3.0",
        )
