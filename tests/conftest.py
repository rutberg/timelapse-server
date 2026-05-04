from __future__ import annotations

import importlib
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def tmp_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TIMELAPSE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv(
        "TIMELAPSE_ALLOWED_NETWORKS",
        "127.0.0.0/8,::1/128",
    )
    return tmp_path


@pytest.fixture
def client(tmp_data_dir: Path) -> Iterator[TestClient]:
    import app.main as server_main

    importlib.reload(server_main)
    with TestClient(server_main.app) as test_client:
        yield test_client
