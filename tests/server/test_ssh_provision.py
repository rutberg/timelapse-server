from pathlib import Path
from unittest.mock import patch

import pytest

from app.ssh_provision import (
    ProvisionError,
    ProvisionTarget,
    resolve_target,
    run_provision,
)


def test_resolve_prefers_hostname(monkeypatch):
    def fake_gethostbyname(host: str) -> str:
        if host == "timelapse-x.local":
            return "192.168.1.50"
        raise OSError("not resolved")

    monkeypatch.setattr("app.ssh_provision.socket.gethostbyname", fake_gethostbyname)
    target = resolve_target(expected_hostname="timelapse-x", ip_fallback="10.0.0.99")
    assert target.host == "timelapse-x.local"
    assert target.resolved_ip == "192.168.1.50"


def test_resolve_falls_back_to_ip(monkeypatch):
    def fake_gethostbyname(host: str) -> str:
        raise OSError("nope")

    monkeypatch.setattr("app.ssh_provision.socket.gethostbyname", fake_gethostbyname)
    target = resolve_target(expected_hostname="timelapse-x", ip_fallback="10.0.0.99")
    assert target.host == "10.0.0.99"


def test_resolve_raises_when_no_route(monkeypatch):
    def fake_gethostbyname(host: str) -> str:
        raise OSError("nope")

    monkeypatch.setattr("app.ssh_provision.socket.gethostbyname", fake_gethostbyname)
    with pytest.raises(ProvisionError):
        resolve_target(expected_hostname="x", ip_fallback=None)


def test_run_provision_invokes_expected_subprocess_calls(tmp_path: Path):
    private_key = tmp_path / "id_ed25519"
    private_key.write_text("PRIV", encoding="utf-8")
    private_key.chmod(0o600)

    target = ProvisionTarget(host="timelapse-x.local", resolved_ip="192.168.1.50")
    script = "#!/usr/bin/env bash\necho hi\n"
    payload_files = {
        "timelapse_agent.py": tmp_path / "agent.py",
        "timelapse-agent.service": tmp_path / "service",
    }
    payload_files["timelapse_agent.py"].write_text("print('hi')", encoding="utf-8")
    payload_files["timelapse-agent.service"].write_text("[Unit]", encoding="utf-8")

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        from subprocess import CompletedProcess
        return CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch("app.ssh_provision.subprocess.run", side_effect=fake_run):
        run_provision(
            target=target,
            ssh_user="pi",
            private_key_path=private_key,
            known_hosts_path=tmp_path / "known_hosts",
            install_script=script,
            payload_files=payload_files,
        )

    flat_args = [" ".join(c) for c in calls]
    assert any("mkdir" in args and "/tmp/timelapse-provision" in args for args in flat_args)
    assert any("scp" in c[0] for c in calls)
    assert any("bash -s" in args for args in flat_args)


def test_run_provision_raises_on_nonzero_exit(tmp_path: Path):
    private_key = tmp_path / "id_ed25519"
    private_key.write_text("PRIV", encoding="utf-8")
    private_key.chmod(0o600)

    target = ProvisionTarget(host="x.local", resolved_ip=None)

    def fake_run(cmd, **kwargs):
        from subprocess import CalledProcessError
        raise CalledProcessError(1, cmd, stderr="boom")

    with patch("app.ssh_provision.subprocess.run", side_effect=fake_run):
        with pytest.raises(ProvisionError, match="boom"):
            run_provision(
                target=target,
                ssh_user="pi",
                private_key_path=private_key,
                known_hosts_path=tmp_path / "known_hosts",
                install_script="echo",
                payload_files={},
            )
