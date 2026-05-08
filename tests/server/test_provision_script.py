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


def test_script_omits_max_pending_bytes_so_agent_auto_detects():
    # Agents now auto-size the pending cap to ~50% of the work_dir
    # partition. The install script should NOT bake an explicit value
    # so this auto behavior kicks in.
    script = build_install_script(
        camera_id="x",
        server_url="http://x:8080",
        agent_version="0.3.0",
    )
    assert "max_pending_bytes" not in script


def test_script_omits_sudo_bootstrap_when_no_password():
    script = build_install_script(
        camera_id="x",
        server_url="http://x:8080",
        agent_version="0.3.0",
    )
    assert "NOPASSWD_EOF" not in script
    assert "010_timelapse-nopasswd" not in script


def test_script_includes_sudo_bootstrap_when_password_present():
    script = build_install_script(
        camera_id="x",
        server_url="http://x:8080",
        agent_version="0.3.0",
        ssh_user="pi",
        sudo_password="hunter2",
    )
    assert "SUDO_PASSWORD='hunter2'" in script
    assert "sudo -S -p ''" in script
    assert "NOPASSWD_EOF" in script
    assert "pi ALL=(ALL) NOPASSWD:ALL" in script
    assert "/etc/sudoers.d/010_timelapse-nopasswd" in script
    # Bootstrap must come before any non-bootstrap sudo so apt-get can run unattended.
    assert script.index("NOPASSWD_EOF") < script.index("sudo apt-get update")


def test_script_escapes_single_quote_in_password():
    script = build_install_script(
        camera_id="x",
        server_url="http://x:8080",
        agent_version="0.3.0",
        ssh_user="pi",
        sudo_password="it's-a-secret",
    )
    # Standard POSIX trick: end the literal, append escaped quote, restart literal.
    assert "SUDO_PASSWORD='it'\\''s-a-secret'" in script


def test_script_rejects_password_with_newline():
    import pytest
    with pytest.raises(ValueError, match="newlines"):
        build_install_script(
            camera_id="x",
            server_url="http://x:8080",
            agent_version="0.3.0",
            sudo_password="line1\nline2",
        )


def test_script_rejects_invalid_ssh_user():
    import pytest
    with pytest.raises(ValueError, match="ssh_user"):
        build_install_script(
            camera_id="x",
            server_url="http://x:8080",
            agent_version="0.3.0",
            ssh_user="root; rm -rf /",
        )


def test_script_includes_ram_pending_dir_for_tmpfs_queue():
    script = build_install_script(
        camera_id="x",
        server_url="http://x:8080",
        agent_version="0.3.0",
    )
    assert '"ram_pending_dir": "/run/timelapse-agent/pending"' in script
