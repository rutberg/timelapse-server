from __future__ import annotations

import json
import re
from textwrap import dedent

VALID_SERVER_URL_RE = re.compile(r"^https?://[A-Za-z0-9._-]+(?::\d+)?(?:/[A-Za-z0-9._/~-]*)?$")
VALID_VERSION_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def build_install_script(camera_id: str, server_url: str, agent_version: str) -> str:
    if not VALID_SERVER_URL_RE.match(server_url):
        raise ValueError(f"Invalid server_url: {server_url!r}")
    if not VALID_VERSION_RE.match(agent_version):
        raise ValueError(f"Invalid agent_version: {agent_version!r}")
    if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}$", camera_id):
        raise ValueError(f"Invalid camera_id: {camera_id!r}")

    config_json = json.dumps(
        {
            "camera_id": camera_id,
            "server_url": server_url,
            "config_poll_seconds": 60,
            "work_dir": "/var/lib/timelapse-agent",
        },
        indent=2,
    )

    return dedent(
        """\
        #!/usr/bin/env bash
        set -euo pipefail

        AGENT_VERSION="{agent_version}"
        INSTALL_ROOT=/opt/timelapse-agent
        VERSION_DIR="$INSTALL_ROOT/$AGENT_VERSION"

        sudo apt-get update
        sudo apt-get install -y rpicam-apps-lite python3

        sudo install -d -m 755 "$VERSION_DIR" /etc/timelapse-agent /var/lib/timelapse-agent

        sudo install -m 755 /tmp/timelapse-provision/timelapse_agent.py "$VERSION_DIR/timelapse_agent.py"
        sudo ln -sfn "$VERSION_DIR" "$INSTALL_ROOT/current"

        sudo install -m 644 /tmp/timelapse-provision/timelapse-agent.service /etc/systemd/system/timelapse-agent.service

        sudo tee /etc/timelapse-agent/config.json >/dev/null <<'TIMELAPSE_CONFIG_EOF'
        {config_json}
        TIMELAPSE_CONFIG_EOF
        sudo chmod 644 /etc/timelapse-agent/config.json

        sudo systemctl daemon-reload
        sudo systemctl enable --now timelapse-agent
        """
    ).format(agent_version=agent_version, config_json=config_json)
