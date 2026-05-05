from unittest.mock import patch, MagicMock
import subprocess

import pytest

from timelapse_agent import (
    gphoto2_available,
    resolve_active_backend,
)


class TestGphoto2Available:
    def test_returns_false_when_binary_missing(self):
        with patch("timelapse_agent.shutil.which", return_value=None):
            assert gphoto2_available() is False

    def test_returns_false_when_no_camera_detected(self):
        # `gphoto2 --auto-detect` exits 0 even with no camera; output has only the header.
        empty_output = "Model                          Port\n----------------------------------------------------------\n"
        completed = MagicMock(returncode=0, stdout=empty_output, stderr="")
        with patch("timelapse_agent.shutil.which", return_value="/usr/bin/gphoto2"), \
             patch("timelapse_agent.subprocess.run", return_value=completed):
            assert gphoto2_available() is False

    def test_returns_true_when_camera_listed(self):
        output = (
            "Model                          Port\n"
            "----------------------------------------------------------\n"
            "Canon EOS R6                   usb:001,005\n"
        )
        completed = MagicMock(returncode=0, stdout=output, stderr="")
        with patch("timelapse_agent.shutil.which", return_value="/usr/bin/gphoto2"), \
             patch("timelapse_agent.subprocess.run", return_value=completed):
            assert gphoto2_available() is True

    def test_returns_false_on_subprocess_error(self):
        with patch("timelapse_agent.shutil.which", return_value="/usr/bin/gphoto2"), \
             patch("timelapse_agent.subprocess.run",
                   side_effect=subprocess.TimeoutExpired(cmd="gphoto2", timeout=5)):
            assert gphoto2_available() is False


class TestResolveActiveBackend:
    def test_explicit_rpicam(self):
        with patch("timelapse_agent.find_capture_command", return_value="/usr/bin/rpicam-still"), \
             patch("timelapse_agent.gphoto2_available", return_value=True):
            assert resolve_active_backend({"camera_backend": "rpicam"}) == "rpicam"

    def test_explicit_gphoto2(self):
        with patch("timelapse_agent.find_capture_command", return_value=None), \
             patch("timelapse_agent.gphoto2_available", return_value=True):
            assert resolve_active_backend({"camera_backend": "gphoto2"}) == "gphoto2"

    def test_auto_prefers_gphoto2_when_camera_detected(self):
        with patch("timelapse_agent.find_capture_command", return_value="/usr/bin/rpicam-still"), \
             patch("timelapse_agent.gphoto2_available", return_value=True):
            assert resolve_active_backend({"camera_backend": "auto"}) == "gphoto2"

    def test_auto_falls_back_to_rpicam_when_no_dslr(self):
        with patch("timelapse_agent.find_capture_command", return_value="/usr/bin/rpicam-still"), \
             patch("timelapse_agent.gphoto2_available", return_value=False):
            assert resolve_active_backend({"camera_backend": "auto"}) == "rpicam"

    def test_auto_returns_none_when_nothing_available(self):
        with patch("timelapse_agent.find_capture_command", return_value=None), \
             patch("timelapse_agent.gphoto2_available", return_value=False):
            assert resolve_active_backend({"camera_backend": "auto"}) is None

    def test_missing_field_treated_as_auto(self):
        with patch("timelapse_agent.find_capture_command", return_value="/usr/bin/rpicam-still"), \
             patch("timelapse_agent.gphoto2_available", return_value=False):
            assert resolve_active_backend({}) == "rpicam"
