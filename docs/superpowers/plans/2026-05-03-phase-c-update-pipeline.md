# Phase C: Agent Update Pipeline — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Prerequisite:** Phase A complete and merged. Phase B is **not** required for Phase C — the two are independent and can run in parallel.

**Goal:** Server stores release tarballs and a `desired_agent_version` per camera. Agent polls the manifest endpoint, downloads the tarball, verifies sha256, atomically swaps `/opt/timelapse-agent/current`, and exits so systemd restarts it on the new code.

**Architecture:** Bundle = `timelapse-agent-<version>.tar.gz` containing a single top-level directory `timelapse-agent-<version>/` with `timelapse_agent.py` and `VERSION`. Bundles + sidecar `*.sha256` files live under `$TIMELAPSE_DATA_DIR/releases/`. Atomic install: extract → swap symlink → exit; systemd `Restart=always` brings the new code up. Failure: roll back the symlink, set `last_error`, keep running old code.

**Tech Stack:** stdlib `tarfile`, `hashlib`, `urllib.request`. No new deps.

---

## Wave structure

```
Wave 1 (sequential):  C1 (build script)
Wave 2 (parallel):    [C2 server endpoints]   [C3 → C4 agent install logic]
Wave 3 (sequential):  C5 (agent loop wiring) → C6 (docs)
```

C2 runs on `server/app/main.py`. C3 + C4 run sequentially on `agent/timelapse_agent.py`. They touch different files; one subagent per branch.

---

## File structure

**New files:**
- `scripts/build-release.sh` — package `agent/` into `dist/timelapse-agent-<version>.tar.gz` with sidecar sha256.
- `tests/server/test_update_manifest.py`
- `tests/server/test_release_serving.py`
- `tests/agent/test_download.py`
- `tests/agent/test_install_bundle.py`
- `tests/agent/test_update_loop.py`
- `tests/scripts/test_build_release.sh` (bash test, runs in CI manually for now).

**Modified files:**
- `server/app/main.py` — `desired_agent_version` field, `/update-manifest` endpoint, `/releases/{filename}` endpoint.
- `agent/timelapse_agent.py` — `download_bundle`, `verify_sha256`, `install_bundle`, `check_for_update` invoked in the poll loop.
- `agent/VERSION` — text file holding the current version. Bumped to `0.3.0` to match `AGENT_VERSION` set in Phase A.

---

### Task C1: Release bundle build script (wave 1)

**Files:**
- Create: `scripts/build-release.sh`
- Create: `agent/VERSION` containing `0.3.0\n`.

- [ ] **Step 1: Create `agent/VERSION`**

```bash
echo "0.3.0" > agent/VERSION
```

- [ ] **Step 2: Create `scripts/build-release.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  scripts/build-release.sh [--out DIR]

Reads agent/VERSION, packages agent/timelapse_agent.py and VERSION into
DIR/timelapse-agent-<version>.tar.gz with a sidecar .sha256 file.

Defaults:
  DIR = dist
USAGE
}

OUT_DIR="dist"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --out) OUT_DIR="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION_FILE="$REPO_ROOT/agent/VERSION"

if [ ! -f "$VERSION_FILE" ]; then
  echo "Missing $VERSION_FILE" >&2
  exit 1
fi

VERSION="$(tr -d '[:space:]' < "$VERSION_FILE")"
if [ -z "$VERSION" ]; then
  echo "VERSION file is empty" >&2
  exit 1
fi

if ! [[ "$VERSION" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "Invalid VERSION: $VERSION" >&2
  exit 1
fi

mkdir -p "$OUT_DIR"
STAGE_DIR="$(mktemp -d)"
trap 'rm -rf "$STAGE_DIR"' EXIT

PKG_DIR="$STAGE_DIR/timelapse-agent-$VERSION"
mkdir -p "$PKG_DIR"
cp "$REPO_ROOT/agent/timelapse_agent.py" "$PKG_DIR/"
cp "$VERSION_FILE" "$PKG_DIR/VERSION"

TARBALL="$OUT_DIR/timelapse-agent-$VERSION.tar.gz"
tar -C "$STAGE_DIR" -czf "$TARBALL" "timelapse-agent-$VERSION"

if command -v sha256sum >/dev/null 2>&1; then
  sha256sum "$TARBALL" | awk '{print $1}' > "$TARBALL.sha256"
else
  shasum -a 256 "$TARBALL" | awk '{print $1}' > "$TARBALL.sha256"
fi

echo "Built $TARBALL"
echo "SHA256: $(cat "$TARBALL.sha256")"
```

- [ ] **Step 3: Make it executable and run it**

```bash
chmod +x scripts/build-release.sh
scripts/build-release.sh --out /tmp/timelapse-build-test
ls -la /tmp/timelapse-build-test
```
Expected: produces `timelapse-agent-0.3.0.tar.gz` and `timelapse-agent-0.3.0.tar.gz.sha256`.

- [ ] **Step 4: Verify bundle structure**

```bash
tar -tzf /tmp/timelapse-build-test/timelapse-agent-0.3.0.tar.gz
```
Expected output:
```
timelapse-agent-0.3.0/
timelapse-agent-0.3.0/timelapse_agent.py
timelapse-agent-0.3.0/VERSION
```

- [ ] **Step 5: Add `dist/` to `.gitignore`**

Append a line `dist/` to `.gitignore`.

- [ ] **Step 6: Commit**

```bash
git add scripts/build-release.sh agent/VERSION .gitignore
git commit -m "feat: release bundle build script"
```

---

### Task C2: Server-side update manifest + release serving (wave 2, server branch)

**Files:**
- Modify: `server/app/main.py`
- Test: `tests/server/test_update_manifest.py`
- Test: `tests/server/test_release_serving.py`

This task has three sub-parts on a single file; one subagent does them in order.

**Part C2a — Add `desired_agent_version` to `CameraConfig`.**

- [ ] **Step 1: Write the failing test**

`tests/server/test_update_manifest.py`:
```python
def test_camera_config_accepts_desired_agent_version(client):
    response = client.put(
        "/api/cameras/tomatoes/config",
        json={
            "enabled": True,
            "interval_seconds": 600,
            "image_width": None,
            "image_height": None,
            "jpeg_quality": 85,
            "desired_agent_version": "0.3.1",
        },
    )
    assert response.status_code == 200
    assert response.json()["desired_agent_version"] == "0.3.1"
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/server/test_update_manifest.py -v`
Expected: FAIL — Pydantic rejects unknown field, or value is dropped.

- [ ] **Step 3: Add the field**

In `server/app/main.py` `CameraConfig`:
```python
class CameraConfig(BaseModel):
    enabled: bool = True
    interval_seconds: int = Field(900, ge=30, le=86_400)
    image_width: Optional[int] = Field(None, ge=320, le=10_000)
    image_height: Optional[int] = Field(None, ge=240, le=10_000)
    jpeg_quality: int = Field(85, ge=1, le=100)
    desired_agent_version: Optional[str] = Field(None, pattern=r"^[A-Za-z0-9._-]+$")
```

- [ ] **Step 4: Run test**

Run: `pytest tests/server/test_update_manifest.py::test_camera_config_accepts_desired_agent_version -v`
Expected: PASS.

**Part C2b — `/update-manifest` endpoint.**

- [ ] **Step 5: Write the failing tests**

Append to `tests/server/test_update_manifest.py`:
```python
def test_manifest_404_when_no_desired_version(client):
    response = client.get("/api/cameras/tomatoes/update-manifest")
    assert response.status_code == 404


def test_manifest_returns_url_and_sha_when_release_present(client, tmp_data_dir):
    releases = tmp_data_dir / "releases"
    releases.mkdir(parents=True, exist_ok=True)
    bundle = releases / "timelapse-agent-0.3.1.tar.gz"
    bundle.write_bytes(b"fake-tarball")
    sha = releases / "timelapse-agent-0.3.1.tar.gz.sha256"
    sha.write_text("deadbeef\n", encoding="utf-8")

    client.put(
        "/api/cameras/tomatoes/config",
        json={
            "enabled": True,
            "interval_seconds": 600,
            "image_width": None,
            "image_height": None,
            "jpeg_quality": 85,
            "desired_agent_version": "0.3.1",
        },
    )

    response = client.get("/api/cameras/tomatoes/update-manifest")
    assert response.status_code == 200
    body = response.json()
    assert body["version"] == "0.3.1"
    assert body["url"].endswith("/api/releases/timelapse-agent-0.3.1.tar.gz")
    assert body["sha256"] == "deadbeef"


def test_manifest_503_when_release_missing(client):
    client.put(
        "/api/cameras/tomatoes/config",
        json={
            "enabled": True,
            "interval_seconds": 600,
            "image_width": None,
            "image_height": None,
            "jpeg_quality": 85,
            "desired_agent_version": "9.9.9",
        },
    )
    response = client.get("/api/cameras/tomatoes/update-manifest")
    assert response.status_code == 503
```

- [ ] **Step 6: Run to verify they fail**

Run: `pytest tests/server/test_update_manifest.py -v`
Expected: 404s.

- [ ] **Step 7: Add the endpoint**

```python
RELEASES_DIR_NAME = "releases"


def release_paths(version: str) -> Tuple[Path, Path]:
    if not re.match(r"^[A-Za-z0-9._-]+$", version):
        raise HTTPException(status_code=400, detail="Invalid version")
    bundle = DATA_DIR / RELEASES_DIR_NAME / f"timelapse-agent-{version}.tar.gz"
    sha = bundle.with_suffix(bundle.suffix + ".sha256")
    return bundle, sha


@app.get("/api/cameras/{camera_id}/update-manifest")
def get_update_manifest(camera_id: str, request: Request) -> Dict[str, Any]:
    camera_id = safe_identifier(camera_id)
    config = get_camera_config(camera_id)
    desired = config.desired_agent_version
    if not desired:
        raise HTTPException(status_code=404, detail="No desired agent version configured")
    bundle, sha = release_paths(desired)
    if not bundle.exists() or not sha.exists():
        raise HTTPException(status_code=503, detail=f"Release {desired} not staged on server")
    base_url = str(request.base_url).rstrip("/")
    return {
        "version": desired,
        "url": f"{base_url}/api/releases/timelapse-agent-{desired}.tar.gz",
        "sha256": sha.read_text(encoding="utf-8").strip(),
    }
```

- [ ] **Step 8: Run tests**

Run: `pytest tests/server/test_update_manifest.py -v`
Expected: 4 passed.

**Part C2c — `/api/releases/{filename}` serving endpoint.**

- [ ] **Step 9: Write the failing tests**

`tests/server/test_release_serving.py`:
```python
def test_serves_existing_release(client, tmp_data_dir):
    releases = tmp_data_dir / "releases"
    releases.mkdir(parents=True, exist_ok=True)
    (releases / "timelapse-agent-0.3.1.tar.gz").write_bytes(b"binary-bytes")

    response = client.get("/api/releases/timelapse-agent-0.3.1.tar.gz")
    assert response.status_code == 200
    assert response.content == b"binary-bytes"
    assert response.headers["content-type"] == "application/gzip"


def test_release_404_when_missing(client):
    response = client.get("/api/releases/timelapse-agent-9.9.9.tar.gz")
    assert response.status_code == 404


def test_release_400_for_path_traversal(client):
    response = client.get("/api/releases/..%2Fconfig.json")
    assert response.status_code in (400, 404)
```

- [ ] **Step 10: Run to verify they fail**

Run: `pytest tests/server/test_release_serving.py -v`
Expected: 404s for the first two.

- [ ] **Step 11: Add the endpoint**

```python
RELEASE_FILENAME_RE = re.compile(r"^timelapse-agent-[A-Za-z0-9._-]+\.tar\.gz$")


@app.get("/api/releases/{filename}")
def serve_release(filename: str) -> FileResponse:
    if not RELEASE_FILENAME_RE.match(filename):
        raise HTTPException(status_code=400, detail="Invalid release filename")
    path = (DATA_DIR / RELEASES_DIR_NAME / filename).resolve()
    releases_root = (DATA_DIR / RELEASES_DIR_NAME).resolve()
    if not str(path).startswith(str(releases_root) + os.sep) and path != releases_root:
        raise HTTPException(status_code=400, detail="Invalid path")
    if not path.exists():
        raise HTTPException(status_code=404, detail="Release not found")
    return FileResponse(path, media_type="application/gzip", filename=filename)
```

- [ ] **Step 12: Run all server tests**

Run: `pytest tests/server -v`
Expected: all pass.

- [ ] **Step 13: Commit**

```bash
git add server/app/main.py tests/server/test_update_manifest.py tests/server/test_release_serving.py
git commit -m "feat(server): desired_agent_version field, update manifest, release serving"
```

---

### Task C3: Agent download + verify (wave 2, agent branch)

**Files:**
- Modify: `agent/timelapse_agent.py`
- Test: `tests/agent/test_download.py`

- [ ] **Step 1: Write the failing tests**

`tests/agent/test_download.py`:
```python
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
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/agent/test_download.py -v`
Expected: AttributeError — symbols missing.

- [ ] **Step 3: Add the symbols to `agent/timelapse_agent.py`**

Near the top after imports:
```python
import hashlib
import tarfile


class UpdateError(RuntimeError):
    pass
```

After `post_json`:
```python
def download_bundle(url: str, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / Path(url).name
    request = Request(url, method="GET")
    with urlopen(request, timeout=120) as response:
        target.write_bytes(response.read())
    return target


def verify_sha256(path: Path, expected_hex: str) -> None:
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual.lower() != expected_hex.lower():
        raise UpdateError(f"sha256 mismatch: expected {expected_hex}, got {actual}")
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/agent/test_download.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add agent/timelapse_agent.py tests/agent/test_download.py
git commit -m "feat(agent): bundle download + sha256 verification"
```

---

### Task C4: Agent extract + atomic install (wave 2, agent branch)

**Files:**
- Modify: `agent/timelapse_agent.py`
- Test: `tests/agent/test_install_bundle.py`

- [ ] **Step 1: Write the failing tests**

`tests/agent/test_install_bundle.py`:
```python
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
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/agent/test_install_bundle.py -v`
Expected: AttributeError.

- [ ] **Step 3: Add `install_bundle` to `agent/timelapse_agent.py`**

```python
def _safe_extract(tar: tarfile.TarFile, dest: Path) -> None:
    dest_resolved = dest.resolve()
    for member in tar.getmembers():
        member_path = (dest / member.name).resolve()
        try:
            member_path.relative_to(dest_resolved)
        except ValueError as error:
            raise UpdateError(f"unsafe path in bundle: {member.name}") from error
        if member.issym() or member.islnk():
            raise UpdateError(f"unsafe symlink in bundle: {member.name}")
    tar.extractall(dest)


def install_bundle(bundle_path: Path, version: str, install_root: Path) -> Path:
    install_root.mkdir(parents=True, exist_ok=True)
    staging = install_root / f".{version}.staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()
    try:
        with tarfile.open(bundle_path, "r:gz") as tar:
            _safe_extract(tar, staging)
        extracted = list(staging.iterdir())
        if len(extracted) != 1 or not extracted[0].is_dir():
            raise UpdateError("bundle must contain exactly one top-level directory")
        version_dir = install_root / version
        if version_dir.exists():
            shutil.rmtree(version_dir)
        extracted[0].rename(version_dir)
    finally:
        if staging.exists():
            shutil.rmtree(staging)

    current_link = install_root / "current"
    new_link = install_root / ".current.new"
    if new_link.exists() or new_link.is_symlink():
        new_link.unlink()
    new_link.symlink_to(version_dir)
    new_link.replace(current_link)
    return version_dir
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/agent/test_install_bundle.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add agent/timelapse_agent.py tests/agent/test_install_bundle.py
git commit -m "feat(agent): atomic bundle install with path-traversal protection"
```

---

### Task C5: Update loop integration (wave 3)

**Files:**
- Modify: `agent/timelapse_agent.py`
- Test: `tests/agent/test_update_loop.py`

Read `agent/VERSION` at startup so `AGENT_VERSION` reflects the deployed bundle. Add `check_for_update()` that hits the manifest endpoint, downloads, verifies, installs, and exits 0. Wire into the config-poll branch.

- [ ] **Step 1: Write the failing tests**

`tests/agent/test_update_loop.py`:
```python
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
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/agent/test_update_loop.py -v`
Expected: AttributeError.

- [ ] **Step 3: Replace the static `AGENT_VERSION` constant with file-backed loader**

In `agent/timelapse_agent.py`, replace `AGENT_VERSION = "0.3.0"` with:
```python
def _read_agent_version() -> str:
    here = Path(__file__).resolve().parent
    candidates = [
        here / "VERSION",
        here.parent / "VERSION",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.read_text(encoding="utf-8").strip()
    return "0.0.0-dev"


AGENT_VERSION = _read_agent_version()
```

- [ ] **Step 4: Add `check_for_update`**

```python
DEFAULT_INSTALL_ROOT = Path("/opt/timelapse-agent")


def check_for_update(
    settings: Dict[str, Any],
    install_root: Path = DEFAULT_INSTALL_ROOT,
    work_dir: Optional[Path] = None,
) -> bool:
    work_dir = work_dir or Path(settings.get("work_dir", "/var/lib/timelapse-agent"))
    work_dir.mkdir(parents=True, exist_ok=True)
    download_dir = work_dir / "updates"

    url = settings["server_url"].rstrip("/") + f"/api/cameras/{settings['camera_id']}/update-manifest"
    try:
        manifest = request_json(url, timeout=15)
    except HTTPError as error:
        if error.code in (404, 503):
            return False
        logging.warning("Update manifest fetch failed: %s", error)
        return False
    except (URLError, TimeoutError, json.JSONDecodeError) as error:
        logging.warning("Update manifest fetch failed: %s", error)
        return False

    desired = manifest.get("version")
    bundle_url = manifest.get("url")
    sha = manifest.get("sha256")
    if not desired or not bundle_url or not sha:
        return False
    if desired == AGENT_VERSION:
        return False

    logging.info("Update available: %s -> %s", AGENT_VERSION, desired)
    try:
        bundle_path = download_bundle(bundle_url, download_dir)
        verify_sha256(bundle_path, sha)
        install_bundle(bundle_path, desired, install_root)
    except UpdateError as error:
        logging.error("Update failed: %s", error)
        return False
    finally:
        if download_dir.exists():
            for stale in download_dir.glob("*.tar.gz"):
                stale.unlink(missing_ok=True)

    logging.info("Update installed; exiting for systemd to restart on new version")
    return True
```

- [ ] **Step 5: Wire into `run_agent`**

In `run_agent`, inside the `if now >= next_config_poll:` block, after `post_checkin(settings, state)`, add:
```python
            if check_for_update(settings):
                logging.info("Exiting to allow systemd restart")
                return
```

- [ ] **Step 6: Run all agent tests**

Run: `pytest tests/agent -v`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add agent/timelapse_agent.py tests/agent/test_update_loop.py
git commit -m "feat(agent): poll update manifest, install bundle, exit for systemd restart"
```

---

### Task C6: Documentation

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add release section to README**

Append under `## Usage & API`:

```markdown
### Releasing a new agent version

1. Bump `agent/VERSION`.
2. Build the bundle:
   ```bash
   scripts/build-release.sh --out /srv/timelapse/releases
   ```
3. Set the desired version on each camera:
   ```bash
   curl -X PUT http://<SERVER_IP>:8080/api/cameras/<CAMERA_ID>/config \
     -H 'Content-Type: application/json' \
     -d '{
       "enabled": true,
       "interval_seconds": 600,
       "image_width": null,
       "image_height": null,
       "jpeg_quality": 85,
       "desired_agent_version": "0.3.1"
     }'
   ```
4. Within one poll cycle (default 60s) the agent downloads, verifies, installs, and restarts on the new version. The next heartbeat reports the new `agent_version`.

If the install fails, the agent logs `Update failed: ...` and stays on the old version. Watch with `journalctl -u timelapse-agent -f` on the Pi during rollouts.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: agent release + rollout walkthrough"
```

---

## Self-review checklist

- [ ] Bundle install rejects path traversal and symlink members.
- [ ] sha256 mismatch raises `UpdateError` and aborts install (no partial swap).
- [ ] `desired_agent_version=None` returns 404 (no manifest), not 500.
- [ ] Release filename regex does not allow `..`, `/`, or starting with a dot.
- [ ] Agent does **not** restart itself; it exits and relies on `Restart=always` in the systemd unit (introduced/finalized in Phase B).
- [ ] No new Python dependencies added.
