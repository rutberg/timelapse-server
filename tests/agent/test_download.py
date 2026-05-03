import hashlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import timelapse_agent as agent


def test_verify_sha256_passes_for_matching_hash(tmp_path: Path):
    target = tmp_path / "file.bin"
    target.write_bytes(b"hello-world")
    expected = hashlib.sha256(b"hello-world").hexdigest()
    agent.verify_sha256(target, expected)


def test_verify_sha256_raises_for_mismatch(tmp_path: Path):
    target = tmp_path / "file.bin"
    target.write_bytes(b"hello-world")
    with pytest.raises(agent.UpdateError):
        agent.verify_sha256(target, "0" * 64)


def test_download_bundle_writes_file_and_returns_path(tmp_path: Path):
    payload = b"fake-tar-bytes"

    def fake_urlopen(request, timeout=None):
        response = MagicMock()
        response.read.return_value = payload
        response.__enter__ = lambda self: self
        response.__exit__ = lambda self, *args: None
        return response

    with patch.object(agent, "urlopen", side_effect=fake_urlopen):
        path = agent.download_bundle("http://server/api/releases/x.tar.gz", tmp_path)

    assert path.exists()
    assert path.read_bytes() == payload
