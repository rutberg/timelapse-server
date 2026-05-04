import pytest
from app.main import CameraConfig


class TestScheduleModes:
    def test_default_mode_is_none(self):
        cfg = CameraConfig()
        assert cfg.schedule_mode is None
        assert cfg.schedule_days is None
        assert cfg.light_threshold is None
        assert cfg.display_name is None
        assert cfg.latitude is None
        assert cfg.longitude is None

    def test_hours_mode_requires_capture_hours(self):
        with pytest.raises(ValueError, match="capture_hours"):
            CameraConfig(schedule_mode="hours", capture_hours=None)

    def test_hours_mode_with_hours_ok(self):
        cfg = CameraConfig(schedule_mode="hours", capture_hours=[6, 7, 8])
        assert cfg.capture_hours == [6, 7, 8]

    def test_daylight_mode_clears_capture_hours(self):
        cfg = CameraConfig(schedule_mode="daylight", capture_hours=[1, 2, 3])
        assert cfg.capture_hours is None  # forced to null

    def test_scene_mode_requires_threshold(self):
        with pytest.raises(ValueError, match="light_threshold"):
            CameraConfig(schedule_mode="scene", light_threshold=None)

    def test_scene_mode_clears_capture_hours(self):
        cfg = CameraConfig(schedule_mode="scene", light_threshold=60, capture_hours=[1, 2])
        assert cfg.capture_hours is None
        assert cfg.light_threshold == 60

    def test_scene_mode_threshold_range(self):
        with pytest.raises(ValueError):
            CameraConfig(schedule_mode="scene", light_threshold=300)
        with pytest.raises(ValueError):
            CameraConfig(schedule_mode="scene", light_threshold=-1)

    def test_invalid_mode_rejected(self):
        with pytest.raises(ValueError):
            CameraConfig(schedule_mode="never")

    def test_schedule_days_iso_weekday(self):
        cfg = CameraConfig(schedule_days=[1, 3, 5])
        assert cfg.schedule_days == [1, 3, 5]
        with pytest.raises(ValueError):
            CameraConfig(schedule_days=[0])  # Sunday-as-0 not allowed
        with pytest.raises(ValueError):
            CameraConfig(schedule_days=[8])
        with pytest.raises(ValueError):
            CameraConfig(schedule_days=[1, 1])  # duplicates rejected

    def test_latitude_longitude_range(self):
        cfg = CameraConfig(latitude=59.3, longitude=18.0)
        assert cfg.latitude == 59.3
        with pytest.raises(ValueError):
            CameraConfig(latitude=91.0)
        with pytest.raises(ValueError):
            CameraConfig(longitude=181.0)

    def test_display_name_optional(self):
        cfg = CameraConfig(display_name="Tomato camera")
        assert cfg.display_name == "Tomato camera"
