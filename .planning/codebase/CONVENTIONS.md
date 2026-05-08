# Coding Conventions

**Analysis Date:** 2026-05-08

## Linting / Formatting

**No enforced linter or formatter.** CONTRIBUTING.md explicitly states: "There is no enforced linter or formatter yet. Match the style of the file you are editing."

There is no `.eslintrc`, `.prettierrc`, `ruff.toml`, `.flake8`, or `biome.json`. The only lint-adjacent tool in the pyproject is `pytest`.

In practice the code is clean and consistent — it just isn't checked automatically. The implied rule from CONTRIBUTING.md: **avoid drive-by reformatting** — style changes and behavior changes must be separate commits.

## Python Style

### Future Annotations

Every Python source file opens with `from __future__ import annotations`. This applies to all files in `server/app/` and `agent/timelapse_agent.py`. New files must include this import.

```python
from __future__ import annotations

import json
import logging
...
```

### Typing

All server modules use `from typing import Any, Dict, List, Literal, Optional, Tuple` (capital aliases from `typing`). The render queue module also uses `dict[str, ...]` / `list[str]` lowercase (Python 3.10+ style) in a few places, but this is inconsistent — the dominant style is capital aliases.

`Optional[X]` is used for nullable fields; union syntax (`X | None`) is not used anywhere.

Pydantic `BaseModel` fields use `Optional[T]` with `= None` defaults consistently.

Type hints are present on all public function signatures in `server/app/`. The agent (`timelapse_agent.py`) uses them on most but not all helpers.

### Naming

**Files:** `snake_case.py` for all Python modules (`render_queue.py`, `ssh_provision.py`, `dslr_discovery.py`).

**Functions and methods:** `snake_case`. Private helpers (not meant to be imported) are prefixed with a single underscore: `_validate_capture_hours`, `_require_camera_record`, `_run_job`, `_write`, `_now_iso`.

**Classes:** `PascalCase` (`CameraConfig`, `AgentState`, `RenderRunner`, `DslrPropertyMap`).

**Constants:** `UPPER_SNAKE_CASE` at module level (`VALID_CAMERA_ID_RE`, `ONLINE_GRACE_SECONDS`, `MAX_CONCURRENT_RENDERS`, `FALLBACK_MAX_PENDING_BYTES`, `VAAPI_DEVICE`).

**Pydantic validators:** prefixed `_validate_` for `@field_validator`, `_enforce_` for `@model_validator`.

```python
@field_validator("camera_backend")
@classmethod
def _validate_camera_backend(cls, value):
    ...

@model_validator(mode="after")
def _enforce_mode_invariants(self):
    ...
```

**Test helpers:** module-level helpers in test files use `snake_case` without `test_` prefix (e.g. `load_fixture`, `setting`, `tile`, `_make_job`).

### Imports

Standard library first, then third-party (fastapi, pydantic), then local (`from app.agents import ...`). Imports are grouped by these three levels without blank lines between sub-groups within a level. No `__all__` exports anywhere.

Local imports occasionally appear at function body scope to avoid circular imports or to defer heavy imports:

```python
# In main.py endpoint bodies:
from app.render_queue import resolve_range_preset
from app.agents import KeyArchive
```

### Dataclasses vs Pydantic

- **Pydantic `BaseModel`**: used for all API request/response shapes and Pydantic models in `server/app/main.py`. Validation via `@field_validator` and `@model_validator`.
- **`@dataclass`**: used for internal data transfer objects that don't need validation — `AgentState` (agent), `PendingAgent`, `ProvisionTarget`, `JobState`.

### Data Classes — immutability

`@dataclass` fields are not frozen. Mutation (e.g. `job.status = "running"`, `agent.ip_fallback = ip_fallback`) is used directly rather than creating new instances.

### Docstrings

Docstrings appear on:
- Complex public functions with non-obvious behavior (`detect_lan_ip`, `resolve_public_server_url`, `parse_capture_time`, `solar_window`, `resolve_max_pending_bytes`)
- Pydantic model classes when the schema needs explanation (`DslrTelemetryTile`, `DslrSettingDropdown`, `DslrDiscoveryResultRequest`)
- FastAPI endpoint functions with non-trivial logic

Simple CRUD endpoints and straightforward functions omit docstrings. No `Args:`/`Returns:` sections — prose only.

### Comments

Inline comments explain *why*, not *what*:
```python
# Serialize read-modify-write on the feature endpoint so rapid clicks from the
# UI don't drop updates. FastAPI runs sync `def` endpoints in a thread pool;
# without this lock two concurrent requests can each load the store, modify
# disjoint cameras, and the second save overwrites the first.
_feature_lock = threading.Lock()
```

Section comments use `# ---- Label --------` dash-lines in JavaScript (`app.js`).

### Type Ignore / Noqa

Only two suppression comments exist in the whole codebase:
- `store._write(agent)  # type: ignore[attr-defined]` — accessing a private method across module boundary (`server/app/main.py:1034`)
- `except Exception as exc:  # noqa: BLE001` — broad exception catch in the render worker loop (`server/app/render_queue.py:111`)

Use suppressions sparingly. Prefer fixing the issue.

## Error Handling

### Server (FastAPI)

HTTP errors are raised as `HTTPException` at validation boundaries (guard functions), not inline in handler bodies:

```python
def safe_identifier(value: str) -> str:
    if not value or not VALID_CAMERA_ID_RE.match(value):
        raise HTTPException(status_code=400, detail="...")
    return value

def validate_frame_day(value: str) -> str:
    if not VALID_FRAME_DAY_RE.match(value):
        raise HTTPException(status_code=400, detail="Invalid frame day")
    return value
```

Domain errors (`ValueError`, `KeyError`) from lower-level functions are caught in endpoint handlers and re-raised as `HTTPException`:

```python
try:
    agent = store.get(agent_id)
except KeyError as error:
    raise HTTPException(status_code=404, detail="Agent not found") from error
```

Always use `from error` on re-raises to preserve the exception chain.

`ProvisionError` (a custom `RuntimeError` subclass in `server/app/ssh_provision.py`) is the domain exception for SSH provisioning failures. It is caught in the `/provision` endpoint and translated to `502`.

### Agent (stdlib)

Network errors are swallowed with a `logging.warning` to keep the agent loop running:
```python
except (URLError, HTTPError) as error:
    logging.warning("Heartbeat failed: %s", error)
```

Subprocess failures are caught, logged, and treated as soft failures. The agent never crashes the loop on transient errors.

`UpdateError(RuntimeError)` is a dedicated exception for self-update failures.

### Lower-level modules

`ValueError` for bad input, `RuntimeError` for irrecoverable state. No custom exception hierarchy beyond `ProvisionError` and `UpdateError`.

## Logging

Module: stdlib `logging`. Both server and agent use the standard `logging` module — no third-party logging library.

Log calls use `%s` printf-style formatting (not f-strings) consistently:
```python
logging.warning("Thumbnail generation failed for %s: %s", source, error)
logging.info("Update available: %s -> %s", AGENT_VERSION, desired)
logging.error("Update failed: %s", error)
```

Levels in practice:
- `logging.info`: lifecycle events (update available, update installed)
- `logging.warning`: transient failures that don't stop execution (network errors, thumbnail failures, DSLR setting failures)
- `logging.error`: update installation failures

No structured logging; no log context/correlation IDs.

## Data Persistence

**JSON files via atomic write pattern.** Store writes go to a temp file in the same directory, then `replace()`:

```python
with tempfile.NamedTemporaryFile("w", dir=str(DATA_DIR), delete=False) as temp_file:
    json.dump(store, temp_file, indent=2, sort_keys=True)
    temp_file.write("\n")
    temp_path = Path(temp_file.name)
temp_path.replace(STORE_PATH)
```

JSON is always serialized with `indent=2, sort_keys=True`. UTF-8 encoding is always explicit: `open(..., encoding="utf-8")`.

## Datetime Handling

UTC datetimes are serialized as ISO-8601 strings with `Z` suffix, not `+00:00`:
```python
datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
```

This is a deliberate project convention used consistently across server and agent. The `_now_iso()` helper (`main.py:1202`) encapsulates this pattern for server use.

When parsing agent-supplied timestamps, `replace("Z", "+00:00")` normalizes before `datetime.fromisoformat()`.

## JavaScript (Frontend)

**Plain ES modules, no build step.** Files use `import`/`export` with full `/static/v2/...` URL paths.

**Naming:** `camelCase` functions and variables. `PascalCase` classes (unused currently). `UPPER_SNAKE_CASE` constants.

**Template pattern:** views are functions that write to DOM nodes and return a cleanup function (or nothing). Example from `app.js`:
```javascript
export function registerView(prefix, render) {
    views.set(prefix, render);
}
```

**HTML generation:** string interpolation with `escapeHtml()` for user-supplied values; raw HTML for static structure. No template engines.

## Shell Scripts (`scripts/`)

Shebang: `#!/usr/bin/env bash`. First line after shebang: `set -euo pipefail`. This is the only enforced style rule that comes from CONTRIBUTING.md.

---

*Convention analysis: 2026-05-08*
