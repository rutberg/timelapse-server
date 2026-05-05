# DSLR Camera Settings Module — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose gphoto2 camera controls (ISO, shutter speed, aperture, image format, white balance, initialization settings) via the server config, agent, and a conditional UI section that renders only for gphoto2-backend cameras.

**Architecture:** Two new Pydantic v2 models (`DslrSettings`, `DslrStatus`) are added to `server/app/main.py`. The agent reads available choices on startup via `gphoto2 --get-config`, applies init settings once and sequence settings before each capture, reads telemetry before each checkin, and reports everything through the existing checkin endpoint. The UI conditionally renders a DSLR section (hidden for rpicam cameras) with a read-only telemetry block, initialization settings + re-init button, and capture settings with live-populated dropdowns.

**Tech Stack:** Python 3.11 + FastAPI + Pydantic v2 (server), Python 3.11 + subprocess (agent), vanilla JS ES2020 (UI), pytest + FastAPI TestClient (tests)

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `server/app/main.py` | Modify | Add `DslrSettings`, `DslrStatus` models; extend `CameraConfig`, `CameraStatus`, `CheckinRequest`; update checkin handler |
| `agent/timelapse_agent.py` | Modify | Add `AgentState` DSLR fields; add 5 helper functions; update startup, main loop, `post_checkin` |
| `server/app/static/v2/views/camera.js` | Modify | Conditional DSLR section in settings tab |
| `tests/server/test_dslr_models.py` | Create | Model validation tests |
| `tests/server/test_checkin.py` | Modify | Checkin endpoint DSLR + active_backend tests |
| `tests/agent/test_gphoto2_backend.py` | Modify | New agent helper function tests |

---

## Task 1: Server models and checkin handler

**Files:**
- Create: `tests/server/test_dslr_models.py`
- Modify: `server/app/main.py`
- Modify: `tests/server/test_checkin.py`

- [ ] **Step 1: Write failing model tests**

Create `tests/server/test_dslr_models.py`:

```python
import pytest
from app.main import CameraConfig, CameraStatus, CheckinRequest, DslrSettings, DslrStatus


class TestDslrSettings:
    def test_defaults(self):
        s = DslrSettings()
        assert s.capture_target == "Memory card"
        assert s.drive_mode == "Single"
        assert s.focus_mode == "Manual"
        assert s.shutterspeed is None
        assert s.aperture is None
        assert s.iso is None
        assert s.exposure_compensation is None
        assert s.whitebalance is None
        assert s.image_format is None
        assert s.reinit_token is None

    def test_sequence_fields_accept_strings(self):
        s = DslrSettings(shutterspeed="1/125", aperture="5.6", iso="400")
        assert s.shutterspeed == "1/125"
        assert s.aperture == "5.6"
        assert s.iso == "400"


class TestDslrStatus:
    def test_defaults(self):
        st = DslrStatus()
        assert st.battery_level is None
        assert st.available_shots is None
        assert st.shutter_counter is None
        assert st.exposure_mode is None
        assert st.choices == {}
        assert st.last_reinit_token is None
        assert st.last_init_at is None

    def test_choices_populated(self):
        st = DslrStatus(choices={"iso": ["Auto", "100", "200"], "shutterspeed": ["1/125", "1/250"]})
        assert st.choices["iso"] == ["Auto", "100", "200"]


class TestCameraConfigDslrField:
    def test_dslr_defaults_to_none(self):
        cfg = CameraConfig()
        assert cfg.dslr is None

    def test_dslr_accepts_dslr_settings(self):
        cfg = CameraConfig(dslr={"capture_target": "Memory card", "drive_mode": "Single", "focus_mode": "Manual"})
        assert cfg.dslr.capture_target == "Memory card"

    def test_dslr_roundtrips_via_json(self):
        cfg = CameraConfig(camera_backend="gphoto2", dslr={"iso": "400", "shutterspeed": "1/125"})
        dumped = cfg.model_dump()
        restored = CameraConfig(**dumped)
        assert restored.dslr.iso == "400"
        assert restored.dslr.shutterspeed == "1/125"


class TestCameraStatusDslrField:
    def test_dslr_defaults_to_none(self):
        st = CameraStatus()
        assert st.dslr is None
        assert st.active_backend is None

    def test_dslr_and_active_backend_accepted(self):
        st = CameraStatus(
            active_backend="gphoto2",
            dslr={"battery_level": "87%", "available_shots": 1204, "choices": {"iso": ["100", "200"]}},
        )
        assert st.active_backend == "gphoto2"
        assert st.dslr.battery_level == "87%"
        assert st.dslr.available_shots == 1204


class TestCheckinRequestDslrField:
    def test_dslr_and_active_backend_default_none(self):
        req = CheckinRequest()
        assert req.dslr is None
        assert req.active_backend is None

    def test_dslr_and_active_backend_accepted(self):
        req = CheckinRequest(
            active_backend="gphoto2",
            dslr={"battery_level": "72%", "shutter_counter": 15000, "choices": {}},
        )
        assert req.active_backend == "gphoto2"
        assert req.dslr.shutter_counter == 15000
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /root/timelapse && pytest tests/server/test_dslr_models.py -v
```
Expected: `ImportError` — `DslrSettings`, `DslrStatus` do not exist yet.

- [ ] **Step 3: Add DslrSettings model to server/app/main.py**

Insert before `class CameraConfig` (line 50). Add the two new models:

```python
class DslrSettings(BaseModel):
    capture_target: str = "Memory card"
    drive_mode: str = "Single"
    focus_mode: str = "Manual"
    shutterspeed: Optional[str] = None
    aperture: Optional[str] = None
    iso: Optional[str] = None
    exposure_compensation: Optional[str] = None
    whitebalance: Optional[str] = None
    image_format: Optional[str] = None
    reinit_token: Optional[str] = None


class DslrStatus(BaseModel):
    battery_level: Optional[str] = None
    available_shots: Optional[int] = None
    shutter_counter: Optional[int] = None
    exposure_mode: Optional[str] = None
    choices: Dict[str, List[str]] = Field(default_factory=dict)
    last_reinit_token: Optional[str] = None
    last_init_at: Optional[str] = None
```

- [ ] **Step 4: Add dslr field to CameraConfig**

Inside `CameraConfig`, after the `_validate_camera_backend` validator block (after the closing of that `@field_validator` method), add:

```python
    dslr: Optional[DslrSettings] = None
```

- [ ] **Step 5: Add dslr and active_backend fields to CameraStatus**

Inside `CameraStatus`, after `signal_dbm: Optional[int] = Field(default=None, ge=-120, le=0)`, add:

```python
    active_backend: Optional[str] = None
    dslr: Optional[DslrStatus] = None
```

- [ ] **Step 6: Add dslr and active_backend fields to CheckinRequest**

Inside `CheckinRequest`, after `signal_dbm: Optional[int] = Field(default=None, ge=-120, le=0)`, add:

```python
    active_backend: Optional[str] = None
    dslr: Optional[DslrStatus] = None
```

- [ ] **Step 7: Update checkin handler to write dslr and active_backend**

In the `post_checkin` endpoint handler (around line 705), after the existing `if payload.signal_dbm is not None: status["signal_dbm"] = ...` line, add:

```python
    if payload.active_backend is not None:
        status["active_backend"] = payload.active_backend
    if payload.dslr is not None:
        status["dslr"] = payload.dslr.model_dump()
```

- [ ] **Step 8: Run model tests to confirm they pass**

```bash
cd /root/timelapse && pytest tests/server/test_dslr_models.py -v
```
Expected: All tests pass.

- [ ] **Step 9: Write failing checkin tests**

Add to `tests/server/test_checkin.py`:

```python
class TestCheckinDslrFields:
    def test_checkin_records_active_backend(self, client):
        client.post("/api/cameras/cam1/checkin", json={"active_backend": "gphoto2"})
        record = next(c for c in client.get("/api/cameras").json()["cameras"] if c["camera_id"] == "cam1")
        assert record["status"]["active_backend"] == "gphoto2"

    def test_checkin_records_dslr_status(self, client):
        client.post(
            "/api/cameras/cam1/checkin",
            json={
                "active_backend": "gphoto2",
                "dslr": {
                    "battery_level": "87%",
                    "available_shots": 1204,
                    "shutter_counter": 12483,
                    "exposure_mode": "M",
                    "choices": {"iso": ["Auto", "100", "200"]},
                    "last_reinit_token": "2026-05-05T10:00:00Z",
                    "last_init_at": "2026-05-05T10:00:01Z",
                },
            },
        )
        record = next(c for c in client.get("/api/cameras").json()["cameras"] if c["camera_id"] == "cam1")
        dslr = record["status"]["dslr"]
        assert dslr["battery_level"] == "87%"
        assert dslr["available_shots"] == 1204
        assert dslr["exposure_mode"] == "M"
        assert dslr["choices"]["iso"] == ["Auto", "100", "200"]
        assert dslr["last_reinit_token"] == "2026-05-05T10:00:00Z"

    def test_checkin_without_dslr_leaves_dslr_none(self, client):
        client.post("/api/cameras/cam1/checkin", json={"agent_version": "0.9.0"})
        record = next(c for c in client.get("/api/cameras").json()["cameras"] if c["camera_id"] == "cam1")
        assert record["status"]["dslr"] is None
        assert record["status"]["active_backend"] is None
```

- [ ] **Step 10: Run checkin tests to confirm new tests fail**

```bash
cd /root/timelapse && pytest tests/server/test_checkin.py::TestCheckinDslrFields -v
```
Expected: FAIL — `active_backend` and `dslr` not written by handler yet.

- [ ] **Step 11: Run checkin tests to confirm they pass**

```bash
cd /root/timelapse && pytest tests/server/test_checkin.py -v
```
Expected: All tests pass (including new ones — handler was updated in step 7).

- [ ] **Step 12: Run full test suite**

```bash
cd /root/timelapse && pytest -q
```
Expected: All tests pass.

- [ ] **Step 13: Commit**

```bash
cd /root/timelapse && git add server/app/main.py tests/server/test_dslr_models.py tests/server/test_checkin.py
git commit -m "feat(server): add DslrSettings/DslrStatus models and checkin dslr fields"
```

---

## Task 2: Agent — gphoto2 helper functions

**Files:**
- Modify: `agent/timelapse_agent.py`
- Modify: `tests/agent/test_gphoto2_backend.py`

- [ ] **Step 1: Write failing agent helper tests**

Add to `tests/agent/test_gphoto2_backend.py`:

```python
from timelapse_agent import (
    gphoto2_read_choices,
    gphoto2_apply_init_settings,
    gphoto2_apply_sequence_settings,
    gphoto2_read_telemetry,
)


_CHOICE_OUTPUT = """\
Label: ISO Speed
Readonly: 0
Type: RADIO
Current: 400
Choice: 0 Auto
Choice: 1 100
Choice: 2 200
Choice: 3 400
"""

_CURRENT_OUTPUT = """\
Label: Battery Level
Readonly: 1
Type: TEXT
Current: 87%
"""


class TestGphoto2ReadChoices:
    def test_parses_choice_lines(self):
        completed = MagicMock(returncode=0, stdout=_CHOICE_OUTPUT, stderr="")
        with patch("timelapse_agent.subprocess.run", return_value=completed):
            result = gphoto2_read_choices(["iso"])
        assert result == {"iso": ["Auto", "100", "200", "400"]}

    def test_returns_empty_dict_on_subprocess_error(self):
        with patch("timelapse_agent.subprocess.run", side_effect=subprocess.CalledProcessError(1, "gphoto2")):
            result = gphoto2_read_choices(["iso"])
        assert result == {}

    def test_skips_key_on_timeout(self):
        with patch("timelapse_agent.subprocess.run", side_effect=subprocess.TimeoutExpired("gphoto2", 10)):
            result = gphoto2_read_choices(["iso", "shutterspeed"])
        assert result == {}

    def test_skips_key_with_no_choices(self):
        no_choices = "Label: Battery Level\nReadonly: 1\nType: TEXT\nCurrent: 87%\n"
        completed = MagicMock(returncode=0, stdout=no_choices, stderr="")
        with patch("timelapse_agent.subprocess.run", return_value=completed):
            result = gphoto2_read_choices(["batterylevel"])
        assert result == {}


class TestGphoto2ApplyInitSettings:
    def test_calls_set_config_for_each_init_field(self):
        completed = MagicMock(returncode=0, stdout="", stderr="")
        with patch("timelapse_agent.subprocess.run", return_value=completed) as mock_run:
            gphoto2_apply_init_settings({
                "capture_target": "Memory card",
                "drive_mode": "Single",
                "focus_mode": "Manual",
            })
        calls = [c.args[0] for c in mock_run.call_args_list]
        assert ["gphoto2", "--set-config", "capturetarget=Memory card"] in calls
        assert ["gphoto2", "--set-config", "drivemode=Single"] in calls
        assert ["gphoto2", "--set-config", "focusmode=Manual"] in calls

    def test_swallows_called_process_error(self):
        with patch("timelapse_agent.subprocess.run", side_effect=subprocess.CalledProcessError(1, "gphoto2")):
            gphoto2_apply_init_settings({"capture_target": "Memory card", "drive_mode": "Single", "focus_mode": "Manual"})


class TestGphoto2ApplySequenceSettings:
    def test_calls_set_config_for_non_none_fields(self):
        completed = MagicMock(returncode=0, stdout="", stderr="")
        with patch("timelapse_agent.subprocess.run", return_value=completed) as mock_run:
            gphoto2_apply_sequence_settings({"iso": "400", "shutterspeed": "1/125", "aperture": None})
        calls = [c.args[0] for c in mock_run.call_args_list]
        assert ["gphoto2", "--set-config", "iso=400"] in calls
        assert ["gphoto2", "--set-config", "shutterspeed=1/125"] in calls
        assert not any("aperture" in str(c) for c in calls)

    def test_skips_all_none_fields(self):
        with patch("timelapse_agent.subprocess.run") as mock_run:
            gphoto2_apply_sequence_settings({})
        mock_run.assert_not_called()

    def test_continues_after_per_key_failure(self):
        def raise_on_iso(cmd, **_kwargs):
            if "iso=" in cmd[2]:
                raise subprocess.CalledProcessError(1, "gphoto2")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("timelapse_agent.subprocess.run", side_effect=raise_on_iso) as mock_run:
            gphoto2_apply_sequence_settings({"iso": "400", "shutterspeed": "1/125"})
        assert mock_run.call_count == 2


class TestGphoto2ReadTelemetry:
    def test_parses_battery_and_shots(self):
        def fake_run(cmd, **_kwargs):
            key = cmd[2]
            outputs = {
                "batterylevel": "Label: Battery Level\nReadonly: 1\nType: TEXT\nCurrent: 87%\n",
                "availableshots": "Label: Available Shots\nReadonly: 1\nType: TEXT\nCurrent: 1204\n",
                "shuttercounter": "Label: Shutter Counter\nReadonly: 1\nType: TEXT\nCurrent: 12483\n",
                "autoexposuremode": "Label: Exposure Mode\nReadonly: 1\nType: TEXT\nCurrent: M\n",
            }
            return MagicMock(returncode=0, stdout=outputs.get(key, ""), stderr="")

        with patch("timelapse_agent.subprocess.run", side_effect=fake_run):
            result = gphoto2_read_telemetry()

        assert result["battery_level"] == "87%"
        assert result["available_shots"] == 1204
        assert result["shutter_counter"] == 12483
        assert result["exposure_mode"] == "M"

    def test_returns_none_fields_on_subprocess_failure(self):
        with patch("timelapse_agent.subprocess.run", side_effect=subprocess.CalledProcessError(1, "gphoto2")):
            result = gphoto2_read_telemetry()
        assert result["battery_level"] is None
        assert result["available_shots"] is None
        assert result["shutter_counter"] is None
        assert result["exposure_mode"] is None
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /root/timelapse && pytest tests/agent/test_gphoto2_backend.py::TestGphoto2ReadChoices tests/agent/test_gphoto2_backend.py::TestGphoto2ApplyInitSettings tests/agent/test_gphoto2_backend.py::TestGphoto2ApplySequenceSettings tests/agent/test_gphoto2_backend.py::TestGphoto2ReadTelemetry -v
```
Expected: `ImportError` — new functions not defined yet.

- [ ] **Step 3: Add constants and helper functions to agent/timelapse_agent.py**

After `gphoto2_disable_autopoweroff` (after line 700, before `measure_camera_pending`), add:

```python
_DSLR_INIT_KEY_MAP: Dict[str, str] = {
    "capture_target": "capturetarget",
    "drive_mode": "drivemode",
    "focus_mode": "focusmode",
}

_DSLR_SEQUENCE_KEY_MAP: Dict[str, str] = {
    "shutterspeed": "shutterspeed",
    "aperture": "aperture",
    "iso": "iso",
    "exposure_compensation": "exposurecompensation",
    "whitebalance": "whitebalance",
    "image_format": "imageformat",
}

_DSLR_CHOICE_KEYS: List[str] = [
    "shutterspeed", "aperture", "iso", "exposurecompensation",
    "whitebalance", "imageformat", "capturetarget", "drivemode", "focusmode",
]


def gphoto2_read_choices(keys: Optional[List[str]] = None) -> Dict[str, List[str]]:
    if keys is None:
        keys = _DSLR_CHOICE_KEYS
    choices: Dict[str, List[str]] = {}
    for key in keys:
        try:
            result = subprocess.run(
                ["gphoto2", "--get-config", key],
                check=True, capture_output=True, text=True, timeout=10,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
            continue
        values = []
        for line in result.stdout.splitlines():
            if line.startswith("Choice:"):
                parts = line.split(None, 2)
                if len(parts) >= 3:
                    values.append(parts[2])
        if values:
            choices[key] = values
    return choices


def gphoto2_apply_init_settings(dslr: Dict[str, Any]) -> None:
    for field_name, gphoto_key in _DSLR_INIT_KEY_MAP.items():
        value = dslr.get(field_name)
        if value is None:
            continue
        try:
            subprocess.run(
                ["gphoto2", "--set-config", f"{gphoto_key}={value}"],
                check=True, capture_output=True, text=True, timeout=10,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as err:
            logging.warning("Could not set DSLR init setting %s=%s: %s", gphoto_key, value, err)


def gphoto2_apply_sequence_settings(dslr: Dict[str, Any]) -> None:
    for field_name, gphoto_key in _DSLR_SEQUENCE_KEY_MAP.items():
        value = dslr.get(field_name)
        if value is None:
            continue
        try:
            subprocess.run(
                ["gphoto2", "--set-config", f"{gphoto_key}={value}"],
                check=True, capture_output=True, text=True, timeout=10,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as err:
            logging.warning("Could not set DSLR sequence setting %s=%s: %s", gphoto_key, value, err)


def _gphoto2_get_current(key: str) -> Optional[str]:
    try:
        result = subprocess.run(
            ["gphoto2", "--get-config", key],
            check=True, capture_output=True, text=True, timeout=10,
        )
        for line in result.stdout.splitlines():
            if line.startswith("Current:"):
                return line.split(":", 1)[1].strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def gphoto2_read_telemetry() -> Dict[str, Any]:
    shots_str = _gphoto2_get_current("availableshots")
    counter_str = _gphoto2_get_current("shuttercounter")
    return {
        "battery_level": _gphoto2_get_current("batterylevel"),
        "available_shots": int(shots_str) if shots_str and shots_str.isdigit() else None,
        "shutter_counter": int(counter_str) if counter_str and counter_str.isdigit() else None,
        "exposure_mode": _gphoto2_get_current("autoexposuremode"),
    }
```

- [ ] **Step 4: Run agent helper tests to confirm they pass**

```bash
cd /root/timelapse && pytest tests/agent/test_gphoto2_backend.py::TestGphoto2ReadChoices tests/agent/test_gphoto2_backend.py::TestGphoto2ApplyInitSettings tests/agent/test_gphoto2_backend.py::TestGphoto2ApplySequenceSettings tests/agent/test_gphoto2_backend.py::TestGphoto2ReadTelemetry -v
```
Expected: All tests pass.

- [ ] **Step 5: Run full test suite**

```bash
cd /root/timelapse && pytest -q
```
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
cd /root/timelapse && git add agent/timelapse_agent.py tests/agent/test_gphoto2_backend.py
git commit -m "feat(agent): add gphoto2 settings helpers (read_choices, apply_init, apply_sequence, read_telemetry)"
```

---

## Task 3: Agent — integration (AgentState + startup + main loop + post_checkin)

**Files:**
- Modify: `agent/timelapse_agent.py`
- Modify: `tests/agent/test_gphoto2_backend.py`

- [ ] **Step 1: Write failing integration tests**

Add to `tests/agent/test_gphoto2_backend.py`:

```python
from timelapse_agent import (
    AgentState,
    post_checkin,
    gphoto2_apply_init_settings,
    gphoto2_read_choices,
)


class TestAgentStateDslrFields:
    def test_dslr_fields_default_to_none(self):
        state = AgentState()
        assert state.active_backend is None
        assert state.dslr_choices is None
        assert state.dslr_telemetry is None
        assert state.last_reinit_token is None
        assert state.last_init_at is None


class TestPostCheckinDslrPayload:
    def test_includes_active_backend_and_dslr_when_gphoto2(self):
        state = AgentState()
        state.active_backend = "gphoto2"
        state.dslr_choices = {"iso": ["100", "200"]}
        state.dslr_telemetry = {"battery_level": "80%", "available_shots": 500, "shutter_counter": 100, "exposure_mode": "M"}
        state.last_reinit_token = "tok1"
        state.last_init_at = "2026-05-05T10:00:00+00:00"

        captured = {}
        def fake_post_json(url, payload):
            captured.update(payload)

        settings = {"server_url": "http://x", "camera_id": "c1"}
        with patch("timelapse_agent.post_json", side_effect=fake_post_json), \
             patch("timelapse_agent.read_wifi_rssi", return_value=None):
            post_checkin(settings, state)

        assert captured["active_backend"] == "gphoto2"
        assert captured["dslr"]["battery_level"] == "80%"
        assert captured["dslr"]["choices"] == {"iso": ["100", "200"]}
        assert captured["dslr"]["last_reinit_token"] == "tok1"

    def test_dslr_payload_is_none_for_rpicam(self):
        state = AgentState()
        state.active_backend = "rpicam"

        captured = {}
        def fake_post_json(url, payload):
            captured.update(payload)

        settings = {"server_url": "http://x", "camera_id": "c1"}
        with patch("timelapse_agent.post_json", side_effect=fake_post_json), \
             patch("timelapse_agent.read_wifi_rssi", return_value=None):
            post_checkin(settings, state)

        assert captured["dslr"] is None
        assert captured["active_backend"] == "rpicam"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /root/timelapse && pytest tests/agent/test_gphoto2_backend.py::TestAgentStateDslrFields tests/agent/test_gphoto2_backend.py::TestPostCheckinDslrPayload -v
```
Expected: FAIL — `AgentState` has no `active_backend`, `post_checkin` payload lacks new fields.

- [ ] **Step 3: Add DSLR fields to AgentState**

In `agent/timelapse_agent.py`, the `AgentState` dataclass (lines 56–65), add five new fields after `current_light`:

```python
@dataclass
class AgentState:
    last_capture_at: Optional[str] = None
    last_upload_at: Optional[str] = None
    last_error: Optional[str] = None
    pending_count: int = 0
    pending_bytes: int = 0
    in_schedule: bool = True
    local_hour: int = 0
    current_light: Optional[int] = None
    active_backend: Optional[str] = None
    dslr_choices: Optional[Dict[str, Any]] = None
    dslr_telemetry: Optional[Dict[str, Any]] = None
    last_reinit_token: Optional[str] = None
    last_init_at: Optional[str] = None
```

- [ ] **Step 4: Update post_checkin to include active_backend and dslr**

Replace the `payload` dict in `post_checkin` (lines 448–460):

```python
    dslr_payload: Optional[Dict[str, Any]] = None
    if state.active_backend == "gphoto2":
        tel = state.dslr_telemetry or {}
        dslr_payload = {
            "battery_level": tel.get("battery_level"),
            "available_shots": tel.get("available_shots"),
            "shutter_counter": tel.get("shutter_counter"),
            "exposure_mode": tel.get("exposure_mode"),
            "choices": state.dslr_choices or {},
            "last_reinit_token": state.last_reinit_token,
            "last_init_at": state.last_init_at,
        }
    payload = {
        "agent_version": AGENT_VERSION,
        "hostname": socket.gethostname(),
        "last_capture_at": state.last_capture_at,
        "last_upload_at": state.last_upload_at,
        "last_error": state.last_error,
        "pending_count": state.pending_count,
        "pending_bytes": state.pending_bytes,
        "in_schedule": state.in_schedule,
        "local_hour": state.local_hour,
        "current_light": state.current_light,
        "signal_dbm": read_wifi_rssi(),
        "active_backend": state.active_backend,
        "dslr": dslr_payload,
    }
```

- [ ] **Step 5: Run integration tests to confirm they pass**

```bash
cd /root/timelapse && pytest tests/agent/test_gphoto2_backend.py::TestAgentStateDslrFields tests/agent/test_gphoto2_backend.py::TestPostCheckinDslrPayload -v
```
Expected: All pass.

- [ ] **Step 6: Update run_agent startup block**

Replace the 2-line startup block (lines 956–957):

```python
    if resolve_active_backend(remote_config) == "gphoto2":
        gphoto2_disable_autopoweroff()
```

With:

```python
    active_backend = resolve_active_backend(remote_config)
    state.active_backend = active_backend
    if active_backend == "gphoto2":
        gphoto2_disable_autopoweroff()
        dslr_config = remote_config.get("dslr") or {}
        state.dslr_choices = gphoto2_read_choices()
        if dslr_config:
            gphoto2_apply_init_settings(dslr_config)
            state.last_reinit_token = dslr_config.get("reinit_token")
            state.last_init_at = now_local_iso()
```

- [ ] **Step 7: Update main loop — re-init detection and telemetry**

Inside the `if now >= next_config_poll:` block, after `remote_config = fetch_remote_config(settings, cache_path)` and before `post_checkin(settings, state)`, insert:

```python
            if state.active_backend == "gphoto2":
                dslr_config = remote_config.get("dslr") or {}
                new_token = dslr_config.get("reinit_token")
                if new_token != state.last_reinit_token:
                    gphoto2_apply_init_settings(dslr_config)
                    state.dslr_choices = gphoto2_read_choices()
                    state.last_reinit_token = new_token
                    state.last_init_at = now_local_iso()
                    logging.info("DSLR re-initialized (token=%s)", new_token)
                state.dslr_telemetry = gphoto2_read_telemetry()
```

- [ ] **Step 8: Update main loop — sequence settings before capture**

Inside `if enabled and in_schedule and now >= next_capture:`, before the `try:` block containing `capture_frame`, insert:

```python
            if state.active_backend == "gphoto2":
                dslr_config = remote_config.get("dslr") or {}
                gphoto2_apply_sequence_settings(dslr_config)
```

- [ ] **Step 9: Run full test suite**

```bash
cd /root/timelapse && pytest -q
```
Expected: All tests pass.

- [ ] **Step 10: Commit**

```bash
cd /root/timelapse && git add agent/timelapse_agent.py tests/agent/test_gphoto2_backend.py
git commit -m "feat(agent): integrate DSLR settings into startup, main loop, and checkin payload"
```

---

## Task 4: UI — DSLR settings section in camera.js

**Files:**
- Modify: `server/app/static/v2/views/camera.js`

No automated tests — validate manually by starting the dev server and loading a gphoto2 camera.

- [ ] **Step 1: Add DSLR constants and helpers before renderSettingsTab**

Insert the following before the `function renderSettingsTab()` definition in `camera.js`:

```javascript
const DSLR_DEFAULTS = {
  shutterspeed: ['bulb','30','25','20','15','13','10','8','6','5','4','3.2','2.5','2','1.6','1.3','1','0.8','0.6','0.5','0.4','0.3','1/4','1/5','1/6','1/8','1/10','1/13','1/15','1/20','1/25','1/30','1/40','1/50','1/60','1/80','1/100','1/125','1/160','1/200','1/250','1/320','1/400','1/500','1/640','1/800','1/1000','1/1250','1/1600','1/2000','1/2500','1/3200','1/4000','1/5000','1/6400','1/8000'],
  aperture: ['1.2','1.4','1.6','1.8','2','2.2','2.5','2.8','3.2','3.5','4','4.5','5','5.6','6.3','7.1','8','9','10','11','13','14','16','18','20','22'],
  iso: ['Auto','100','125','160','200','250','320','400','500','640','800','1000','1250','1600','2000','2500','3200','4000','5000','6400','8000','10000','12800','25600','51200','102400'],
  exposurecompensation: ['-3','-2.6667','-2.3333','-2','-1.6667','-1.3333','-1','-0.6667','-0.3333','0','0.3333','0.6667','1','1.3333','1.6667','2','2.3333','2.6667','3'],
  whitebalance: ['Auto','Daylight','Cloudy','Tungsten','Fluorescent','Flash','Custom','Shade','Color Temperature'],
  imageformat: ['Large Fine JPEG','Large Normal JPEG','Medium Fine JPEG','Small Fine JPEG','RAW','RAW + Large Fine JPEG','cRAW','cRAW + Large Fine JPEG'],
};

const DSLR_INIT_CHOICES = {
  capturetarget: ['Internal RAM','Memory card'],
  drivemode: ['Single','Continuous','Self Timer 2 sec','Self Timer 10 sec'],
  focusmode: ['Manual','One Shot','AI Servo','Single','Servo'],
};

function dslrSelect(id, label, gphotoKey, choices, selected) {
  const opts = ['', ...choices].map(v =>
    `<option value="${escapeHtml(v)}" ${v === (selected || '') ? 'selected' : ''}>${escapeHtml(v) || '— (leave as-is)'}</option>`
  ).join('');
  return `<label class="field"><span class="lbl">${label}</span><select class="input" id="${id}">${opts}</select></label>`;
}

function renderDslrSection(cfg, status) {
  const dslrCfg = cfg.dslr || {};
  const dslrSt = status?.dslr || {};
  const choices = dslrSt.choices || {};
  const expMode = dslrSt.exposure_mode || '—';
  const expBadge = expMode === 'M'
    ? `<span style="color:var(--green,#22c55e)">${escapeHtml(expMode)}</span>`
    : `<span style="color:var(--amber,#f59e0b)">${escapeHtml(expMode)} — manual mode recommended</span>`;
  const reinitPending = dslrCfg.reinit_token && dslrCfg.reinit_token !== dslrSt.last_reinit_token;

  return `
    <div class="card" style="margin-top:14px"><div class="card-b">
      <div class="lbl">DSLR Status</div>
      <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-top:12px;font-size:13px">
        <div><div class="lbl" style="font-size:11px">Battery</div>${escapeHtml(dslrSt.battery_level || '—')}</div>
        <div><div class="lbl" style="font-size:11px">Available shots</div>${dslrSt.available_shots != null ? Number(dslrSt.available_shots).toLocaleString() : '—'}</div>
        <div><div class="lbl" style="font-size:11px">Shutter count</div>${dslrSt.shutter_counter != null ? Number(dslrSt.shutter_counter).toLocaleString() : '—'}</div>
      </div>
      <div style="margin-top:8px;font-size:13px"><span class="lbl" style="font-size:11px">Exposure mode</span> ${expBadge}</div>
    </div></div>

    <div class="card" style="margin-top:14px"><div class="card-b">
      <div class="lbl">Camera Initialization</div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:12px">
        <label class="field"><span class="lbl">Capture target</span>
          <select class="input" id="d-capturetarget">${DSLR_INIT_CHOICES.capturetarget.map(v => `<option value="${escapeHtml(v)}" ${v === (dslrCfg.capture_target || 'Memory card') ? 'selected' : ''}>${escapeHtml(v)}</option>`).join('')}</select>
        </label>
        <label class="field"><span class="lbl">Drive mode</span>
          <select class="input" id="d-drivemode">${DSLR_INIT_CHOICES.drivemode.map(v => `<option value="${escapeHtml(v)}" ${v === (dslrCfg.drive_mode || 'Single') ? 'selected' : ''}>${escapeHtml(v)}</option>`).join('')}</select>
        </label>
        <label class="field"><span class="lbl">Focus mode</span>
          <select class="input" id="d-focusmode">${DSLR_INIT_CHOICES.focusmode.map(v => `<option value="${escapeHtml(v)}" ${v === (dslrCfg.focus_mode || 'Manual') ? 'selected' : ''}>${escapeHtml(v)}</option>`).join('')}</select>
        </label>
      </div>
      <div style="margin-top:14px;display:flex;align-items:center;gap:10px">
        <button class="btn" data-reinit>${reinitPending ? '⏳ Re-initializing…' : 'Re-initialize'}</button>
        ${reinitPending ? '' : '<span class="small" id="reinit-msg"></span>'}
      </div>
    </div></div>

    <div class="card" style="margin-top:14px"><div class="card-b">
      <div class="lbl">Capture Settings</div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:12px">
        ${dslrSelect('d-shutterspeed', 'Shutter speed', 'shutterspeed', choices.shutterspeed || DSLR_DEFAULTS.shutterspeed, dslrCfg.shutterspeed)}
        ${dslrSelect('d-aperture', 'Aperture', 'aperture', choices.aperture || DSLR_DEFAULTS.aperture, dslrCfg.aperture)}
        ${dslrSelect('d-iso', 'ISO', 'iso', choices.iso || DSLR_DEFAULTS.iso, dslrCfg.iso)}
        ${dslrSelect('d-expcomp', 'Exposure comp.', 'exposurecompensation', choices.exposurecompensation || DSLR_DEFAULTS.exposurecompensation, dslrCfg.exposure_compensation)}
        ${dslrSelect('d-wb', 'White balance', 'whitebalance', choices.whitebalance || DSLR_DEFAULTS.whitebalance, dslrCfg.whitebalance)}
        ${dslrSelect('d-fmt', 'Image format', 'imageformat', choices.imageformat || DSLR_DEFAULTS.imageformat, dslrCfg.image_format)}
      </div>
    </div></div>`;
}
```

- [ ] **Step 2: Update renderSettingsTab to use isGphoto2 and hide rpicam-only fields**

Replace the body of `renderSettingsTab()`:

```javascript
function renderSettingsTab() {
  const cfg = camera.config || {};
  const isGphoto2 = cfg.camera_backend === 'gphoto2' || camera.status?.active_backend === 'gphoto2';
  const hide = isGphoto2 ? ' style="display:none"' : '';
  return `
    <div style="padding:24px;max-width:720px">
      <div class="card"><div class="card-b">
        <div class="lbl">Capture</div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:12px">
          <label class="field"><span class="lbl">Display name</span>
            <input class="input sans" id="s-name" value="${escapeHtml(cfg.display_name||"")}" placeholder="${escapeHtml(camera.camera_id)}"/>
          </label>
          <label class="field"><span class="lbl">Interval (seconds)</span>
            <input class="input" id="s-interval" type="number" min="30" max="86400" value="${cfg.interval_seconds||600}"/>
          </label>
          <label class="field"${hide}><span class="lbl">Width (px)</span>
            <input class="input" id="s-width" type="number" min="320" max="10000" value="${cfg.image_width||""}" placeholder="full"/>
          </label>
          <label class="field"${hide}><span class="lbl">Height (px)</span>
            <input class="input" id="s-height" type="number" min="240" max="10000" value="${cfg.image_height||""}" placeholder="full"/>
          </label>
          <label class="field"${hide}><span class="lbl">JPEG quality (1–100)</span>
            <input class="input" id="s-quality" type="number" min="1" max="100" value="${cfg.jpeg_quality||85}"/>
          </label>
          <label class="field"><span class="lbl">Desired agent version</span>
            <input class="input" id="s-version" value="${escapeHtml(cfg.desired_agent_version||"")}" placeholder="latest"/>
          </label>
        </div>
      </div></div>

      ${isGphoto2 ? renderDslrSection(cfg, camera.status) : ''}

      <div class="row" style="margin-top:14px;gap:8px">
        <button class="btn primary" data-save-settings>Save settings</button>
        <span class="small" id="settings-msg"></span>
        <button class="btn danger" style="margin-left:auto" data-delete-camera>${icon("trash",12)}Delete camera</button>
      </div>
    </div>`;
}
```

- [ ] **Step 3: Update wireSettings to include DSLR fields and wire re-init button**

Replace the `wireSettings` function:

```javascript
function wireSettings() {
  const isGphoto2 = (camera.config?.camera_backend === 'gphoto2') || (camera.status?.active_backend === 'gphoto2');

  function buildDslrPayload(withReinit) {
    const existing = camera.config?.dslr || {};
    const val = (id) => { const el = document.getElementById(id); return el ? el.value || null : existing[id] ?? null; };
    const payload = {
      capture_target: document.getElementById('d-capturetarget')?.value || existing.capture_target || 'Memory card',
      drive_mode: document.getElementById('d-drivemode')?.value || existing.drive_mode || 'Single',
      focus_mode: document.getElementById('d-focusmode')?.value || existing.focus_mode || 'Manual',
      shutterspeed: val('d-shutterspeed'),
      aperture: val('d-aperture'),
      iso: val('d-iso'),
      exposure_compensation: val('d-expcomp'),
      whitebalance: val('d-wb'),
      image_format: val('d-fmt'),
      reinit_token: withReinit ? new Date().toISOString() : (existing.reinit_token || null),
    };
    return payload;
  }

  document.querySelector('[data-save-settings]').addEventListener('click', async () => {
    const optNum = (id) => { const v = document.getElementById(id)?.value; return v ? Number(v) : null; };
    const payload = {
      ...camera.config,
      display_name: document.getElementById('s-name').value.trim() || null,
      interval_seconds: Number(document.getElementById('s-interval').value),
      image_width:  optNum('s-width'),
      image_height: optNum('s-height'),
      jpeg_quality: Number(document.getElementById('s-quality').value) || 85,
      desired_agent_version: document.getElementById('s-version').value.trim() || null,
      ...(isGphoto2 ? { dslr: buildDslrPayload(false) } : {}),
    };
    const msg = document.getElementById('settings-msg');
    try {
      camera.config = await api.fetchJson(`/api/cameras/${encId}/config`, { method: 'PUT', body: JSON.stringify(payload) });
      msg.textContent = 'Saved.'; msg.style.color = 'var(--green)';
      invalidateSidebar();
    } catch (e) {
      msg.textContent = e.message; msg.style.color = 'var(--red)';
    }
  });

  const reinitBtn = document.querySelector('[data-reinit]');
  if (reinitBtn) {
    reinitBtn.addEventListener('click', async () => {
      const optNum = (id) => { const v = document.getElementById(id)?.value; return v ? Number(v) : null; };
      const payload = {
        ...camera.config,
        display_name: document.getElementById('s-name').value.trim() || null,
        interval_seconds: Number(document.getElementById('s-interval').value),
        image_width:  optNum('s-width'),
        image_height: optNum('s-height'),
        jpeg_quality: Number(document.getElementById('s-quality').value) || 85,
        desired_agent_version: document.getElementById('s-version').value.trim() || null,
        dslr: buildDslrPayload(true),
      };
      const msgEl = document.getElementById('reinit-msg');
      try {
        camera.config = await api.fetchJson(`/api/cameras/${encId}/config`, { method: 'PUT', body: JSON.stringify(payload) });
        reinitBtn.textContent = '⏳ Re-initializing…';
        reinitBtn.disabled = true;
        if (msgEl) { msgEl.textContent = 'Sent. Camera will re-initialize on next poll.'; msgEl.style.color = 'var(--green)'; }
        invalidateSidebar();
      } catch (e) {
        if (msgEl) { msgEl.textContent = e.message; msgEl.style.color = 'var(--red)'; }
      }
    });
  }

  document.querySelector('[data-delete-camera]').addEventListener('click', () => {
    if (!confirm(`Delete camera "${cameraId}"? This cannot be undone.`)) return;
    api.fetchJson(`/api/cameras/${encId}`, { method: 'DELETE' })
      .then(() => { invalidateSidebar(); window.location.hash = '#/dashboard'; })
      .catch(e => alert(e.message));
  });
}
```

- [ ] **Step 4: Run full test suite (server + agent)**

```bash
cd /root/timelapse && pytest -q
```
Expected: All tests pass (UI changes have no automated tests).

- [ ] **Step 5: Commit**

```bash
cd /root/timelapse && git add server/app/static/v2/views/camera.js
git commit -m "feat(ui): add conditional DSLR settings section with telemetry, init, and capture controls"
```

---

## Push

- [ ] **Push branch and open PR**

```bash
cd /root/timelapse && git push -u origin feature/canon-r6-dslr-support
gh pr create \
  --title "feat: DSLR camera settings module (gphoto2)" \
  --body "$(cat <<'EOF'
## Summary
- Adds `DslrSettings` + `DslrStatus` Pydantic models to server; extends `CameraConfig`, `CameraStatus`, `CheckinRequest`
- Agent reads gphoto2 choices on startup, applies init settings (capture target, drive mode, focus mode) once, applies sequence settings (ISO, shutter, aperture, WB, format) before each capture, reads telemetry (battery, shots, shutter count, exposure mode) before each checkin
- Re-init token mechanism: UI sets a timestamp token; agent echoes it back; UI shows pending spinner until tokens match
- Settings tab conditionally shows DSLR section (hidden for rpicam), hides irrelevant width/height/quality fields for gphoto2 cameras

## Test plan
- [ ] All existing tests pass (`pytest -q`)
- [ ] New server model tests pass (`tests/server/test_dslr_models.py`)
- [ ] New checkin DSLR tests pass (`tests/server/test_checkin.py::TestCheckinDslrFields`)
- [ ] New agent helper tests pass (`tests/agent/test_gphoto2_backend.py`)
- [ ] Manual: load camera with `camera_backend=gphoto2` in UI, confirm DSLR section renders and rpicam fields are hidden
- [ ] Manual: confirm Re-initialize button transitions to pending state after click

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
