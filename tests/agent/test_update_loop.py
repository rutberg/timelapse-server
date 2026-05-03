import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import timelapse_agent as agent


def make_manifest_response(version: str, url: str, sha: str) -> MagicMock:
    response = MagicMock()
    response.read.return_value = json.dumps(
        {"version": version, "url": url, "sha256": sha}
    ).encode("utf-8")
    response.__enter__ = lambda self: self
    response.__exit__ = lambda self, *args: None
    return response


def test_check_for_update_no_op_when_versions_match(tmp_path: Path):
    settings = {"camera_id": "x", "server_url": "http://server"}

    def fake_urlopen(request, timeout=None):
        return make_manifest_response(agent.AGENT_VERSION, "http://server/api/releases/x.tar.gz", "abc")

    with patch.object(agent, "urlopen", side_effect=fake_urlopen), \
         patch.object(agent, "download_bundle") as mock_download:
        applied = agent.check_for_update(settings, install_root=tmp_path / "install", work_dir=tmp_path / "work")

    assert applied is False
    assert mock_download.call_count == 0


def test_check_for_update_downloads_and_installs_new_version(tmp_path: Path):
    settings = {"camera_id": "x", "server_url": "http://server"}
    install_root = tmp_path / "install"
    work_dir = tmp_path / "work"

    def fake_urlopen(request, timeout=None):
        return make_manifest_response("9.9.9", "http://server/api/releases/timelapse-agent-9.9.9.tar.gz", "deadbeef")

    def fake_download(url, dest_dir):
        path = Path(dest_dir) / "timelapse-agent-9.9.9.tar.gz"
        path.write_bytes(b"x")
        return path

    with patch.object(agent, "urlopen", side_effect=fake_urlopen), \
         patch.object(agent, "download_bundle", side_effect=fake_download), \
         patch.object(agent, "verify_sha256") as mock_verify, \
         patch.object(agent, "install_bundle") as mock_install:
        applied = agent.check_for_update(settings, install_root=install_root, work_dir=work_dir)

    assert applied is True
    mock_verify.assert_called_once()
    mock_install.assert_called_once()


def test_check_for_update_handles_404_gracefully(tmp_path: Path):
    settings = {"camera_id": "x", "server_url": "http://server"}

    def fake_urlopen(request, timeout=None):
        from urllib.error import HTTPError
        raise HTTPError("u", 404, "Not Found", {}, None)

    with patch.object(agent, "urlopen", side_effect=fake_urlopen):
        applied = agent.check_for_update(settings, install_root=tmp_path / "i", work_dir=tmp_path / "w")

    assert applied is False
