# Testing Patterns

**Analysis Date:** 2026-05-08

## Test Framework

**Runner:** pytest ≥8, <9
**Config:** `pyproject.toml` `[tool.pytest.ini_options]`
**Async:** pytest-asyncio ≥0.23, <1 — `asyncio_mode = "auto"` (all `async def` test functions run automatically without needing `@pytest.mark.asyncio` on most tests, though some tests still use the decorator explicitly)
**HTTP client:** httpx ≥0.27 (used via FastAPI's `TestClient`)
**Mocking:** stdlib `unittest.mock` (`patch`, `patch.object`, `MagicMock`)

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["server", "agent"]
addopts = "-q"
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
```

The `pythonpath` setting means `from app.main import ...` and `import timelapse_agent` work without installation.

**Run Commands:**
```bash
.venv-dev/bin/python -m pytest           # full suite (quiet output)
.venv-dev/bin/python -m pytest tests/server   # server only
.venv-dev/bin/python -m pytest tests/agent    # agent only
.venv-dev/bin/python -m pytest -k <pattern>   # filter by name
```

There is no coverage command configured; no coverage target is enforced.

## Test File Organization

**Location:** Separate `tests/` directory, mirroring the source split:
```
tests/
├── conftest.py          # shared fixtures (client, tmp_data_dir)
├── __init__.py
├── server/
│   ├── __init__.py
│   ├── fixtures/
│   │   └── dslr/        # JSON payloads (canon-eos-r6.json, nikon-d3400.json, sony-a7m4.json, unknown-vendor.json)
│   └── test_*.py        # ~28 test files
└── agent/
    ├── __init__.py
    └── test_*.py        # ~8 test files
```

**Naming:** `test_<feature_or_module>.py`. One test file per logical feature or endpoint group, not one-per-source-file.

**Test count:** ~390 test functions across both suites.

## Shared Fixtures (conftest.py)

`tests/conftest.py` provides two fixtures used by almost all server tests:

```python
@pytest.fixture
def tmp_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TIMELAPSE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TIMELAPSE_ALLOWED_NETWORKS", "127.0.0.0/8,::1/128")
    return tmp_path

@pytest.fixture
def client(tmp_data_dir: Path) -> Iterator[TestClient]:
    import app.main as server_main
    importlib.reload(server_main)   # forces module-level globals to re-read env
    with TestClient(server_main.app) as test_client:
        yield test_client
```

Key design decisions:
- `importlib.reload(server_main)` re-evaluates module-level globals (like `DATA_DIR`) after `monkeypatch.setenv`. Without this the env var change would be ignored.
- `TestClient` is used as a context manager so the ASGI lifespan events (`startup`/`shutdown`) fire, which starts and stops the `RenderRunner`.
- `tmp_data_dir` returns the `tmp_path` so tests can write directly to the data directory to set up state.

Most server tests take `client` as their only fixture (which pulls in `tmp_data_dir` automatically via fixture dependency).

## Test Structure

**Dominant pattern: standalone functions**

Most tests are standalone `def test_*` functions using the `client` fixture directly. About 54 tests use `class Test*` groupings when testing multiple facets of the same feature.

```python
# Standalone (most common)
def test_checkin_records_status_fields(client):
    response = client.post("/api/cameras/tomatoes/checkin", json={...})
    assert response.status_code == 200
    ...

# Class grouping (for related behavior)
class TestCheckinDslrFields:
    def test_checkin_records_active_backend(self, client):
        ...
    def test_checkin_records_dslr_status(self, client):
        ...
```

**Test anatomy:**
1. Arrange: set up state (POST to API or write files directly to `tmp_data_dir`)
2. Act: make the HTTP call or call the function under test
3. Assert: check `response.status_code` and `response.json()` fields

No `setUp`/`tearDown` — setup is done inline. Each test gets a fresh `tmp_path` via pytest's built-in fixture.

## Mocking

**Two approaches used — choose by what you're testing:**

**`monkeypatch.setattr` (preferred for module-level attributes):**
```python
monkeypatch.setattr(main_module, "detect_lan_ip", lambda: "10.0.0.5")
monkeypatch.setattr("app.render_queue.RECENT_TTL_SECONDS", 0.05)
monkeypatch.setattr(agent.shutil, "disk_usage", lambda p: fake_usage)
```

**`unittest.mock.patch` / `patch.object` (used in agent tests for subprocess/network):**
```python
with patch.object(agent, "urlopen", side_effect=fake_urlopen):
    agent.post_checkin(settings, state)

with patch("timelapse_agent.subprocess.run", return_value=completed), \
     patch("timelapse_agent.post_json", side_effect=fake_post):
    ...
```

**What gets mocked:**
- `urlopen` / `post_json` — network calls in agent tests (never hit real network)
- `subprocess.run` — gphoto2 calls (never require real camera hardware)
- `detect_lan_ip`, `TIMELAPSE_PUBLIC_URL` env var — server URL resolution tests
- `shutil.disk_usage` — disk space calculations
- `app.render_queue.RECENT_TTL_SECONDS` — time-based TTLs for async reaper tests
- `RenderRunner._run_job` — replaced with a coroutine that simulates fast/slow/hanging renders

**What is NOT mocked:**
- Filesystem operations — tests use `tmp_path` / `tmp_data_dir` real temp directories
- FastAPI routing — tests use the real `app` via `TestClient`
- Pydantic validation — never bypassed
- JSON serialization/parsing — always real

## Async Tests

The render queue has async tests using pytest-asyncio. Because `asyncio_mode = "auto"` is set, `async def test_*` runs automatically. Some tests still use `@pytest.mark.asyncio` explicitly (this is harmless but redundant).

**Polling pattern** for async worker tests (instead of `await asyncio.sleep` loops):
```python
for _ in range(200):
    await asyncio.sleep(0.01)
    snap = runner.snapshot()
    if snap["running"] is None and snap["recent"]:
        break
await runner.stop()
```

**Event-based synchronization** for concurrency tests:
```python
started = asyncio.Event()
release = asyncio.Event()

async def slow_run(job):
    started.set()
    await release.wait()

monkeypatch.setattr(runner, "_run_job", slow_run)
await runner.start()
runner.enqueue(j1)
await started.wait()  # j1 is running
```

## Fixture Files

Static JSON fixtures live in `tests/server/fixtures/dslr/`:
- `canon-eos-r6.json` — Canon raw config dump
- `nikon-d3400.json` — Nikon raw config dump
- `sony-a7m4.json` — Sony raw config dump
- `unknown-vendor.json` — fallback vendor test

Loaded via a local helper in `test_dslr_discovery.py`:
```python
FIXTURES = Path(__file__).parent / "fixtures" / "dslr"

def load_fixture(name: str) -> dict:
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items() if not k.startswith("_")}
```

The `_source` key in fixtures is documentation metadata, stripped before use.

## External Dependency Skipping

Tests that require system binaries use `pytest.skip()` or module-level `pytestmark.skipif`:

```python
# Module-level skip (test_ssh_keys.py, test_create_agent_endpoint.py)
pytestmark = pytest.mark.skipif(
    shutil.which("ssh-keygen") is None,
    reason="ssh-keygen not available",
)

# Inline skip (test_render_queue.py, test_video_format.py)
if not shutil.which("ffmpeg"):
    pytest.skip("ffmpeg not installed")
```

All other tests run without any system dependencies. The CI-suitable test suite is the full suite minus the skipped tests.

## Test Types

**Unit tests (majority):**
- Agent logic in isolation: `test_heartbeat.py`, `test_solar.py`, `test_scene_light.py`, `test_schedule_days.py`, `test_dslr_discovery.py` (agent), `test_gphoto2_backend.py`
- Server logic in isolation: `test_dslr_discovery.py` (server), `test_render_queue.py` (pure unit parts), `test_timestamps.py`, `test_public_url.py`
- Data model validation: `test_camera_record.py`, `test_dslr_models.py`, `test_camera_backend_field.py`, `test_camera_id_validation.py`

**Integration tests (via `TestClient`):**
- All `tests/server/test_*.py` files that use the `client` fixture combine the FastAPI routing, request/response serialization, filesystem operations, and data store in one round-trip.
- Examples: `test_checkin.py`, `test_camera_listing.py`, `test_frames_listing.py`, `test_videos_listing.py`, `test_render_queue.py` (endpoint parts)

**System/integration tests requiring external binaries (skip-guarded):**
- `test_ssh_keys.py` — requires `ssh-keygen`
- `test_create_agent_endpoint.py` — requires `ssh-keygen`
- `test_render_queue.py::test_run_job_mp4_produces_file_and_progress` — requires `ffmpeg`
- `test_video_format.py` tests — requires `ffmpeg`

**No E2E tests.** No browser tests, no real-Pi agent tests.

## Coverage Gaps

**Frontend JavaScript:** No tests at all. `server/app/static/v2/` (app.js, views/, components/) has zero test coverage.

**`timelapse_agent.py` integration paths:** `run_agent()` (the main loop, `main.py:1445`) is not tested; it orchestrates many tested individual functions but the loop itself has no test.

**`server/app/main.py` thumbnail generation:** `ensure_frame_thumbnail` is exercised via the upload endpoint in tests but the `ffmpeg`-dependent path is only tested when ffmpeg is installed (skip-guarded).

**SSH provisioning end-to-end:** `test_ssh_provision.py` and `test_provision_endpoint.py` test the logic but not real SSH connections. No integration test against an actual SSH target.

**`scripts/` shell scripts:** No tests.

**Concurrent request behavior:** Only the `_feature_lock` threading lock has explicit concurrency testing consideration (noted in comments); there are no race-condition tests.

---

*Testing analysis: 2026-05-08*
