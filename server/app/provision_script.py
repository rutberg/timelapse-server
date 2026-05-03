from __future__ import annotations

import json
import re
from textwrap import dedent
from typing import Optional

VALID_SERVER_URL_RE = re.compile(r"^https?://[A-Za-z0-9._-]+(?::\d+)?(?:/[A-Za-z0-9._/~-]*)?$")
VALID_VERSION_RE = re.compile(r"^[A-Za-z0-9._-]+$")
VALID_SSH_USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,30}$")


def _shell_single_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def build_install_script(
    camera_id: str,
    server_url: str,
    agent_version: str,
    ssh_user: str = "pi",
    sudo_password: Optional[str] = None,
) -> str:
    if not VALID_SERVER_URL_RE.match(server_url):
        raise ValueError(f"Invalid server_url: {server_url!r}")
    if not VALID_VERSION_RE.match(agent_version):
        raise ValueError(f"Invalid agent_version: {agent_version!r}")
    if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}$", camera_id):
        raise ValueError(f"Invalid camera_id: {camera_id!r}")
    if not VALID_SSH_USER_RE.match(ssh_user):
        raise ValueError(f"Invalid ssh_user: {ssh_user!r}")
    if sudo_password is not None and ("\n" in sudo_password or "\x00" in sudo_password):
        raise ValueError("sudo_password must not contain newlines or null bytes")

    config_json = json.dumps(
        {
            "camera_id": camera_id,
            "server_url": server_url,
            "config_poll_seconds": 60,
            "work_dir": "/var/lib/timelapse-agent",
        },
        indent=2,
    )

    if sudo_password:
        # sudo -S reads the first line of stdin as the password, then bash -s
        # consumes the remaining lines as a script. The heredoc is unquoted
        # so $SUDO_PASSWORD interpolates from the surrounding shell. Single-
        # quoting the password assignment keeps the value literal regardless
        # of metacharacters.
        bootstrap = dedent(
            """\
            SUDO_PASSWORD={password_quoted}
            sudo -S -p '' bash -s <<NOPASSWD_EOF
            $SUDO_PASSWORD
            echo "{ssh_user} ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/010_timelapse-nopasswd
            chmod 440 /etc/sudoers.d/010_timelapse-nopasswd
            NOPASSWD_EOF
            unset SUDO_PASSWORD

            """
        ).format(
            password_quoted=_shell_single_quote(sudo_password),
            ssh_user=ssh_user,
        )
    else:
        bootstrap = ""

    body = dedent(
        """\
        #!/usr/bin/env bash
        set -euo pipefail

        AGENT_VERSION="{agent_version}"
        INSTALL_ROOT=/opt/timelapse-agent
        VERSION_DIR="$INSTALL_ROOT/$AGENT_VERSION"

        {bootstrap}sudo apt-get update
        sudo apt-get install -y rpicam-apps-lite python3

        sudo install -d -m 755 "$VERSION_DIR" /etc/timelapse-agent /var/lib/timelapse-agent

        sudo install -m 755 /tmp/timelapse-provision/timelapse_agent.py "$VERSION_DIR/timelapse_agent.py"
        printf '%s\\n' "$AGENT_VERSION" | sudo tee "$VERSION_DIR/VERSION" >/dev/null
        sudo chmod 644 "$VERSION_DIR/VERSION"
        sudo ln -sfn "$VERSION_DIR" "$INSTALL_ROOT/current"

        sudo install -m 644 /tmp/timelapse-provision/timelapse-agent.service /etc/systemd/system/timelapse-agent.service

        sudo tee /etc/timelapse-agent/config.json >/dev/null <<'TIMELAPSE_CONFIG_EOF'
        {config_json}
        TIMELAPSE_CONFIG_EOF
        sudo chmod 644 /etc/timelapse-agent/config.json

        sudo systemctl daemon-reload
        sudo systemctl enable --now timelapse-agent
        """
    ).format(agent_version=agent_version, config_json=config_json, bootstrap=bootstrap)
    return body
