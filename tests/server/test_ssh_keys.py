import os
import shutil
from pathlib import Path

import pytest

from app.ssh_keys import generate_keypair, read_public_key


pytestmark = pytest.mark.skipif(
    shutil.which("ssh-keygen") is None,
    reason="ssh-keygen not available",
)


def test_generate_keypair_writes_two_files(tmp_path: Path):
    private_path, public_path = generate_keypair(tmp_path, comment="agent-test")

    assert private_path.exists()
    assert public_path.exists()
    assert private_path.name == "id_ed25519"
    assert public_path.name == "id_ed25519.pub"


def test_private_key_is_chmod_600(tmp_path: Path):
    private_path, _ = generate_keypair(tmp_path, comment="agent-test")
    mode = os.stat(private_path).st_mode & 0o777
    assert mode == 0o600


def test_public_key_starts_with_ssh_ed25519(tmp_path: Path):
    _, public_path = generate_keypair(tmp_path, comment="timelapse-agent-test")
    text = read_public_key(public_path)
    assert text.startswith("ssh-ed25519 ")
    assert "timelapse-agent-test" in text
