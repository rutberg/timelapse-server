"""Agent-side discovery handshake + property-map-driven helpers (issue #13)."""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

from timelapse_agent import (
    AgentState,
    gphoto2_apply_init_settings,
    gphoto2_apply_sequence_settings,
    gphoto2_list_config,
    gphoto2_read_telemetry,
    parse_body_info,
    parse_list_all_config,
    post_discovery_result,
    run_dslr_discovery,
)


_LIST_ALL_CONFIG_SAMPLE = """\
/main/status/manufacturer
Label: Camera Manufacturer
Readonly: 1
Type: TEXT
Current: Nikon Corporation
END
/main/status/cameramodel
Label: Camera Model
Readonly: 1
Type: TEXT
Current: D3400
END
/main/imgsettings/iso
Label: ISO Speed
Readonly: 0
Type: RADIO
Current: 400
Choice: 0 100
Choice: 1 200
Choice: 2 400
END
"""


class TestParseListAllConfig:
    def test_parses_blocks(self):
        tree = parse_list_all_config(_LIST_ALL_CONFIG_SAMPLE)
        assert set(tree) == {
            "/main/status/manufacturer",
            "/main/status/cameramodel",
            "/main/imgsettings/iso",
        }
        assert tree["/main/status/manufacturer"]["readonly"] is True
        assert tree["/main/imgsettings/iso"]["choices"] == ["100", "200", "400"]
        assert tree["/main/imgsettings/iso"]["current"] == "400"


class TestParseBodyInfo:
    def test_canon_serial_prefers_eosserialnumber(self):
        tree = {
            "/main/status/manufacturer": {"current": "Canon Inc.", "choices": []},
            "/main/status/cameramodel": {"current": "Canon EOS R6", "choices": []},
            "/main/status/eosserialnumber": {"current": "012345678901", "choices": []},
            "/main/status/serialnumber": {"current": "FALLBACK", "choices": []},
        }
        info = parse_body_info(tree)
        assert info["vendor"] == "Canon"
        assert info["model"] == "Canon EOS R6"
        assert info["serial"] == "012345678901"

    def test_sony_vendor_inferred_from_model(self):
        tree = {
            "/main/status/cameramodel": {"current": "Sony ILCE-7M4", "choices": []},
        }
        info = parse_body_info(tree)
        assert info["vendor"] == "Sony"


class TestGphoto2ListConfig:
    def test_invokes_list_all_config(self):
        completed = MagicMock(returncode=0, stdout=_LIST_ALL_CONFIG_SAMPLE, stderr="")
        with patch("timelapse_agent.subprocess.run", return_value=completed) as mock_run:
            tree = gphoto2_list_config()
        assert "/main/imgsettings/iso" in tree
        assert mock_run.call_args[0][0] == ["gphoto2", "--list-all-config"]


class TestPostDiscoveryResult:
    def test_posts_to_correct_url(self):
        captured = {}

        def fake_post_json(url, payload, timeout=15):
            captured["url"] = url
            captured["payload"] = payload
            return {"acknowledged": True}

        settings = {"server_url": "http://srv", "camera_id": "cam1"}
        with patch("timelapse_agent.post_json", side_effect=fake_post_json):
            post_discovery_result(
                settings, token="tok", raw_config={"/x": {}}, body={"vendor": "Sony"}
            )
        assert captured["url"] == "http://srv/api/cameras/cam1/dslr/discovery/result"
        assert captured["payload"]["token"] == "tok"
        assert captured["payload"]["body"]["vendor"] == "Sony"
        assert "raw_config" in captured["payload"]

    def test_swallows_network_errors(self):
        from urllib.error import URLError

        def boom(*_a, **_kw):
            raise URLError("nope")

        with patch("timelapse_agent.post_json", side_effect=boom):
            # Must not raise — agent shouldn't crash on a missed POST.
            post_discovery_result({"server_url": "http://x", "camera_id": "c"},
                                  token="tok", error="something broke")


class TestRunDslrDiscovery:
    def test_happy_path_runs_list_config_then_posts(self):
        settings = {"server_url": "http://srv", "camera_id": "cam1"}
        state = AgentState()
        completed = MagicMock(returncode=0, stdout=_LIST_ALL_CONFIG_SAMPLE, stderr="")

        captured = {}

        def fake_post(url, payload, timeout=15):
            captured.update(payload)
            return {"acknowledged": True}

        with patch("timelapse_agent.subprocess.run", return_value=completed), \
             patch("timelapse_agent.post_json", side_effect=fake_post):
            run_dslr_discovery(settings, state, token="tok-abc")

        assert state.last_discovery_token == "tok-abc"
        assert captured["token"] == "tok-abc"
        assert captured["body"]["vendor"] == "Nikon"
        assert "/main/imgsettings/iso" in captured["raw_config"]
        # Error key is omitted on success.
        assert "error" not in captured

    def test_error_path_posts_failure_and_marks_token_handled(self):
        settings = {"server_url": "http://srv", "camera_id": "cam1"}
        state = AgentState()
        captured = {}

        def fake_post(url, payload, timeout=15):
            captured.update(payload)
            return {"acknowledged": True}

        with patch("timelapse_agent.subprocess.run",
                   side_effect=subprocess.CalledProcessError(1, "gphoto2", stderr="dead")), \
             patch("timelapse_agent.post_json", side_effect=fake_post):
            run_dslr_discovery(settings, state, token="tok-err")

        # Token marked handled so we don't loop forever on a broken camera.
        assert state.last_discovery_token == "tok-err"
        assert captured["token"] == "tok-err"
        assert "error" in captured


# Property-map-driven helpers — Step 4 of the plan.

_NIKON_PROP_MAP = {
    "schema_version": 1,
    "discovered_at": "2026-05-06T00:00:00Z",
    "body": {"vendor": "Nikon", "model": "D3400", "serial": "x"},
    "telemetry_tiles": [
        {"field": "battery", "label": "Battery",
         "read_key": "batterylevel", "kind": "string"},
        {"field": "exposure_mode", "label": "Exposure mode",
         "read_key": "expprogram", "kind": "string"},
        {"field": "camera_model", "label": "Camera",
         "read_key": "cameramodel", "kind": "string"},
    ],
    "setting_dropdowns": [
        {"settings_field": "shutterspeed", "label": "Shutter speed",
         "read_key": "shutterspeed", "write_key": "shutterspeed2"},
        {"settings_field": "aperture", "label": "Aperture",
         "read_key": "f-number", "write_key": "f-number"},
        {"settings_field": "iso", "label": "ISO",
         "read_key": "iso", "write_key": "iso"},
    ],
    "init_keys": [
        {"settings_field": "drive_mode", "label": "Drive mode",
         "read_key": "capturemode", "write_key": "capturemode"},
        {"settings_field": "focus_mode", "label": "Focus mode",
         "read_key": "focusmode", "write_key": "focusmode"},
    ],
}


_SONY_PROP_MAP = {
    "schema_version": 1,
    "discovered_at": "2026-05-06T00:00:00Z",
    "body": {"vendor": "Sony", "model": "ILCE-7M4", "serial": "x"},
    "telemetry_tiles": [
        # Sony bodies don't expose battery / available shots / lens.
        {"field": "exposure_mode", "label": "Exposure mode",
         "read_key": "expprogram", "kind": "string"},
        {"field": "camera_model", "label": "Camera",
         "read_key": "cameramodel", "kind": "string"},
    ],
    "setting_dropdowns": [
        {"settings_field": "iso", "label": "ISO",
         "read_key": "iso", "write_key": "iso"},
    ],
    "init_keys": [],
}


class TestGphoto2ApplySequenceSettingsWithPropMap:
    def test_nikon_writes_to_shutterspeed2(self):
        completed = MagicMock(returncode=0, stdout="", stderr="")
        with patch("timelapse_agent.subprocess.run", return_value=completed) as mock_run:
            gphoto2_apply_sequence_settings(
                {"shutterspeed": "1/125", "aperture": "f/5.6", "iso": "400"},
                prop_map=_NIKON_PROP_MAP,
            )
        calls = [c.args[0] for c in mock_run.call_args_list]
        # Shutterspeed must be written through `shutterspeed2`, not `shutterspeed`,
        # otherwise Nikon bodies reject the call.
        assert ["gphoto2", "--set-config", "shutterspeed2=1/125"] in calls
        assert not any("shutterspeed=1/125" in str(c) for c in calls)
        assert ["gphoto2", "--set-config", "f-number=f/5.6"] in calls
        assert ["gphoto2", "--set-config", "iso=400"] in calls

    def test_no_prop_map_falls_back_to_legacy_keys(self):
        completed = MagicMock(returncode=0, stdout="", stderr="")
        with patch("timelapse_agent.subprocess.run", return_value=completed) as mock_run:
            gphoto2_apply_sequence_settings({"iso": "400", "shutterspeed": "1/125"})
        calls = [c.args[0] for c in mock_run.call_args_list]
        # Legacy path uses `shutterspeed` (Canon-style).
        assert ["gphoto2", "--set-config", "shutterspeed=1/125"] in calls
        assert ["gphoto2", "--set-config", "iso=400"] in calls


class TestGphoto2ApplyInitSettingsWithPropMap:
    def test_nikon_init_keys_use_capturemode(self):
        completed = MagicMock(returncode=0, stdout="", stderr="")
        with patch("timelapse_agent.subprocess.run", return_value=completed) as mock_run:
            gphoto2_apply_init_settings(
                {"drive_mode": "Single Shot", "focus_mode": "Manual"},
                prop_map=_NIKON_PROP_MAP,
            )
        calls = [c.args[0] for c in mock_run.call_args_list]
        assert ["gphoto2", "--set-config", "capturemode=Single Shot"] in calls
        assert ["gphoto2", "--set-config", "focusmode=Manual"] in calls
        # No drivemode= write — that's the Canon-only key.
        assert not any("drivemode=" in str(c) for c in calls)

    def test_sony_init_keys_empty_writes_nothing(self):
        # Sony's prop_map has init_keys=[], so even if dslr settings are passed
        # we don't try to write them (and can't fail on missing PTP keys).
        with patch("timelapse_agent.subprocess.run") as mock_run:
            gphoto2_apply_init_settings(
                {"drive_mode": "Single Shot", "focus_mode": "Manual"},
                prop_map=_SONY_PROP_MAP,
            )
        mock_run.assert_not_called()


class TestGphoto2ReadTelemetryWithPropMap:
    def test_returns_only_keys_in_map(self):
        # Sony map: only exposure_mode + camera_model. battery/shots/lens are absent.
        outputs = {
            "expprogram": "Label: Exp\nReadonly: 1\nType: TEXT\nCurrent: M\n",
            "cameramodel": "Label: Cam\nReadonly: 1\nType: TEXT\nCurrent: ILCE-7M4\n",
        }

        def fake_run(cmd, **_kwargs):
            key = cmd[2]
            return MagicMock(returncode=0, stdout=outputs.get(key, ""), stderr="")

        with patch("timelapse_agent.subprocess.run", side_effect=fake_run):
            result = gphoto2_read_telemetry(prop_map=_SONY_PROP_MAP)
        assert set(result) == {"exposure_mode", "camera_model"}
        assert result["exposure_mode"] == "M"
        assert result["camera_model"] == "ILCE-7M4"
        # No `battery` / `available_shots` keys at all — Sony's status card
        # won't render those tiles.
        assert "battery" not in result
        assert "available_shots" not in result

    def test_legacy_no_prop_map_returns_canon_shape(self):
        # Sanity check: pre-discovery cameras keep getting today's shape.
        with patch("timelapse_agent.subprocess.run",
                   side_effect=subprocess.CalledProcessError(1, "gphoto2")):
            result = gphoto2_read_telemetry()
        assert "battery_level" in result
        assert "available_shots" in result
        assert "exposure_mode" in result
        assert "camera_model" in result
