import pytest
from app.main import CameraConfig


class TestCameraBackendField:
    def test_default_is_auto(self):
        cfg = CameraConfig()
        assert cfg.camera_backend == "auto"

    def test_accepts_rpicam(self):
        cfg = CameraConfig(camera_backend="rpicam")
        assert cfg.camera_backend == "rpicam"

    def test_accepts_gphoto2(self):
        cfg = CameraConfig(camera_backend="gphoto2")
        assert cfg.camera_backend == "gphoto2"

    def test_accepts_auto(self):
        cfg = CameraConfig(camera_backend="auto")
        assert cfg.camera_backend == "auto"

    def test_rejects_unknown_backend(self):
        with pytest.raises(ValueError, match="camera_backend"):
            CameraConfig(camera_backend="webcam")

    def test_rejects_empty_string(self):
        with pytest.raises(ValueError, match="camera_backend"):
            CameraConfig(camera_backend="")
