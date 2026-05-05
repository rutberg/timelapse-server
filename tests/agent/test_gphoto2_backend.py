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


from timelapse_agent import (
    CameraFileRef,
    parse_new_file_location,
    gphoto2_capture_trigger,
)


class TestParseNewFileLocation:
    def test_parses_standard_canon_output(self):
        output = (
            "New file is in location /store_00020001/DCIM/100CANON/IMG_0042.CR3 on the camera\n"
        )
        ref = parse_new_file_location(output)
        assert ref == CameraFileRef(
            folder="/store_00020001/DCIM/100CANON",
            filename="IMG_0042.CR3",
        )

    def test_parses_jpeg_output(self):
        output = "New file is in location /store_00010001/DCIM/100CANON/IMG_0099.JPG on the camera\n"
        ref = parse_new_file_location(output)
        assert ref.filename == "IMG_0099.JPG"
        assert ref.folder == "/store_00010001/DCIM/100CANON"

    def test_parses_when_line_is_not_last(self):
        output = (
            "Some progress noise\n"
            "New file is in location /a/b/IMG_1.CR3 on the camera\n"
            "Saving file...\n"
        )
        ref = parse_new_file_location(output)
        assert ref.folder == "/a/b"
        assert ref.filename == "IMG_1.CR3"

    def test_returns_none_when_no_match(self):
        assert parse_new_file_location("No file here\n") is None
        assert parse_new_file_location("") is None


class TestGphoto2CaptureTrigger:
    def test_runs_capture_image_and_returns_ref(self):
        completed = MagicMock(
            returncode=0,
            stdout="New file is in location /a/b/IMG_5.CR3 on the camera\n",
            stderr="",
        )
        with patch("timelapse_agent.subprocess.run", return_value=completed) as mock_run:
            ref = gphoto2_capture_trigger()
        assert ref == CameraFileRef(folder="/a/b", filename="IMG_5.CR3")
        args, kwargs = mock_run.call_args
        assert args[0] == ["gphoto2", "--capture-image"]
        assert kwargs.get("check") is True
        assert kwargs.get("capture_output") is True
        assert kwargs.get("text") is True

    def test_raises_when_output_unparseable(self):
        completed = MagicMock(returncode=0, stdout="No new file line\n", stderr="")
        with patch("timelapse_agent.subprocess.run", return_value=completed):
            with pytest.raises(RuntimeError, match="parse"):
                gphoto2_capture_trigger()

    def test_propagates_subprocess_error(self):
        with patch("timelapse_agent.subprocess.run",
                   side_effect=subprocess.CalledProcessError(1, "gphoto2", stderr="cam offline")):
            with pytest.raises(subprocess.CalledProcessError):
                gphoto2_capture_trigger()
