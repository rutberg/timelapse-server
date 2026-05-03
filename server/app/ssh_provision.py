from __future__ import annotations

import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional


class ProvisionError(RuntimeError):
    pass


@dataclass
class ProvisionTarget:
    host: str
    resolved_ip: Optional[str]


def resolve_target(expected_hostname: str, ip_fallback: Optional[str]) -> ProvisionTarget:
    hostname_candidate = f"{expected_hostname}.local"
    try:
        ip = socket.gethostbyname(hostname_candidate)
        return ProvisionTarget(host=hostname_candidate, resolved_ip=ip)
    except OSError:
        pass
    if ip_fallback:
        return ProvisionTarget(host=ip_fallback, resolved_ip=ip_fallback)
    raise ProvisionError(
        f"Could not resolve {expected_hostname!r} or fallback IP. "
        "Confirm the Pi finished first boot, joined the LAN, and that the "
        "hostname or IP is correct."
    )


def _quoted_known_hosts(known_hosts_path: Path) -> str:
    # OpenSSH parses UserKnownHostsFile as a space-separated list of paths.
    # Wrap in double quotes so paths containing spaces (e.g. "Local Projects")
    # stay a single value. Backslash-escape any embedded double quotes.
    safe = str(known_hosts_path).replace("\\", "\\\\").replace('"', '\\"')
    return f'UserKnownHostsFile="{safe}"'


def _ssh_base_args(
    ssh_user: str,
    private_key_path: Path,
    known_hosts_path: Path,
    host: str,
) -> list:
    return [
        "ssh",
        "-i", str(private_key_path),
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", _quoted_known_hosts(known_hosts_path),
        f"{ssh_user}@{host}",
    ]


def _scp_base_args(
    ssh_user: str,
    private_key_path: Path,
    known_hosts_path: Path,
    host: str,
    sources: list,
    destination: str,
) -> list:
    return [
        "scp",
        "-i", str(private_key_path),
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", _quoted_known_hosts(known_hosts_path),
        *[str(src) for src in sources],
        f"{ssh_user}@{host}:{destination}",
    ]


def run_provision(
    target: ProvisionTarget,
    ssh_user: str,
    private_key_path: Path,
    known_hosts_path: Path,
    install_script: str,
    payload_files: Dict[str, Path],
) -> None:
    known_hosts_path.parent.mkdir(parents=True, exist_ok=True)
    known_hosts_path.touch(exist_ok=True)

    try:
        # Clean and create staging dir on the Pi.
        subprocess.run(
            _ssh_base_args(ssh_user, private_key_path, known_hosts_path, target.host)
            + ["rm -rf /tmp/timelapse-provision && mkdir -p /tmp/timelapse-provision"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )

        # Copy payload files.
        if payload_files:
            subprocess.run(
                _scp_base_args(
                    ssh_user,
                    private_key_path,
                    known_hosts_path,
                    target.host,
                    list(payload_files.values()),
                    "/tmp/timelapse-provision/",
                ),
                check=True,
                capture_output=True,
                text=True,
                timeout=120,
            )

        # Pipe install script through bash.
        subprocess.run(
            _ssh_base_args(ssh_user, private_key_path, known_hosts_path, target.host)
            + ["bash -s"],
            check=True,
            input=install_script,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except subprocess.CalledProcessError as error:
        message = error.stderr or error.stdout or str(error)
        raise ProvisionError(message.strip()) from error
    except subprocess.TimeoutExpired as error:
        raise ProvisionError(f"SSH timeout: {error}") from error
