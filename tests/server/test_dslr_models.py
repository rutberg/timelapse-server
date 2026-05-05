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
