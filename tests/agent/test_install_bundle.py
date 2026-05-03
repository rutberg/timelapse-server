import io
import tarfile
from pathlib import Path

import pytest

import timelapse_agent as agent


def make_bundle(tmp_path: Path, version: str) -> Path:
    bundle_path = tmp_path / f"timelapse-agent-{version}.tar.gz"
    with tarfile.open(bundle_path, "w:gz") as tar:
        for relative_name, body in (
            ("timelapse_agent.py", "AGENT_VERSION = 'x'\n"),
            ("VERSION", version),
        ):
            data = body.encode("utf-8")
            info = tarfile.TarInfo(name=f"timelapse-agent-{version}/{relative_name}")
            info.size = len(data)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
    return bundle_path


def test_install_bundle_creates_versioned_dir_and_swaps_symlink(tmp_path: Path):
    install_root = tmp_path / "install"
    install_root.mkdir()
    bundle = make_bundle(tmp_path, "0.3.1")

    agent.install_bundle(bundle, "0.3.1", install_root)

    version_dir = install_root / "0.3.1"
    current = install_root / "current"
    assert version_dir.is_dir()
    assert (version_dir / "timelapse_agent.py").exists()
    assert current.is_symlink()
    assert current.resolve() == version_dir.resolve()


def test_install_bundle_rejects_unsafe_member(tmp_path: Path):
    install_root = tmp_path / "install"
    install_root.mkdir()
    bundle_path = tmp_path / "bad.tar.gz"
    with tarfile.open(bundle_path, "w:gz") as tar:
        info = tarfile.TarInfo(name="../escape")
        data = b"x"
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))

    with pytest.raises(agent.UpdateError, match="unsafe"):
        agent.install_bundle(bundle_path, "0.3.1", install_root)


def test_install_bundle_idempotent(tmp_path: Path):
    install_root = tmp_path / "install"
    install_root.mkdir()
    bundle = make_bundle(tmp_path, "0.3.1")

    agent.install_bundle(bundle, "0.3.1", install_root)
    agent.install_bundle(bundle, "0.3.1", install_root)

    assert (install_root / "0.3.1" / "VERSION").read_text(encoding="utf-8") == "0.3.1"


def test_install_bundle_skips_macos_metadata(tmp_path: Path):
    install_root = tmp_path / "install"
    install_root.mkdir()
    bundle_path = tmp_path / "with-metadata.tar.gz"
    with tarfile.open(bundle_path, "w:gz") as tar:
        for relative_name, body in (
            ("timelapse-agent-0.4.0/timelapse_agent.py", "AGENT_VERSION = 'x'\n"),
            ("timelapse-agent-0.4.0/VERSION", "0.4.0"),
            # Cruft that macOS BSD tar adds when archiving directories
            # with extended attributes:
            ("._timelapse-agent-0.4.0", "applesingle-junk"),
            ("timelapse-agent-0.4.0/._VERSION", "applesingle-junk"),
            ("timelapse-agent-0.4.0/.DS_Store", "spotlight-junk"),
            ("__MACOSX/timelapse-agent-0.4.0/._VERSION", "junk"),
        ):
            data = body.encode("utf-8")
            info = tarfile.TarInfo(name=relative_name)
            info.size = len(data)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))

    agent.install_bundle(bundle_path, "0.4.0", install_root)

    version_dir = install_root / "0.4.0"
    assert version_dir.is_dir()
    assert (version_dir / "VERSION").read_text(encoding="utf-8") == "0.4.0"
    assert not (version_dir / "._VERSION").exists()
    assert not (version_dir / ".DS_Store").exists()
