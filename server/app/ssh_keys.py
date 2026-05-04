from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Tuple


SAFE_COMMENT_RE = re.compile(r"[^A-Za-z0-9._-]+")


def generate_keypair(target_dir: Path, comment: str) -> Tuple[Path, Path]:
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    private_path = target_dir / "id_ed25519"
    public_path = target_dir / "id_ed25519.pub"

    if private_path.exists() or public_path.exists():
        raise FileExistsError(f"Keypair already exists in {target_dir}")

    sanitized_comment = SAFE_COMMENT_RE.sub("-", comment).strip("-") or "timelapse-agent"

    subprocess.run(
        [
            "ssh-keygen",
            "-t", "ed25519",
            "-N", "",
            "-f", str(private_path),
            "-C", sanitized_comment,
        ],
        check=True,
        capture_output=True,
    )
    os.chmod(private_path, 0o600)
    os.chmod(public_path, 0o644)
    return private_path, public_path


def read_public_key(public_path: Path) -> str:
    return Path(public_path).read_text(encoding="utf-8").strip()
