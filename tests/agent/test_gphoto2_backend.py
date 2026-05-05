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


from timelapse_agent import (
    PENDING_CAMERA_FILES_NAME,
    load_camera_pending,
    save_camera_pending,
    add_camera_pending,
    remove_camera_pending,
)


class TestCameraPendingState:
    def test_load_returns_empty_when_file_missing(self, tmp_path):
        assert load_camera_pending(tmp_path) == []

    def test_save_then_load_roundtrip(self, tmp_path):
        entries = [
            {"folder": "/a", "filename": "IMG_1.CR3", "captured_at": "2026-05-05T10:00:00-07:00"},
            {"folder": "/a", "filename": "IMG_2.CR3", "captured_at": "2026-05-05T10:01:00-07:00"},
        ]
        save_camera_pending(tmp_path, entries)
        assert load_camera_pending(tmp_path) == entries

    def test_save_writes_to_expected_filename(self, tmp_path):
        save_camera_pending(tmp_path, [])
        assert (tmp_path / PENDING_CAMERA_FILES_NAME).exists()

    def test_add_appends_entry(self, tmp_path):
        add_camera_pending(
            tmp_path,
            CameraFileRef(folder="/a", filename="X.CR3"),
            captured_at="2026-05-05T10:00:00-07:00",
        )
        add_camera_pending(
            tmp_path,
            CameraFileRef(folder="/a", filename="Y.CR3"),
            captured_at="2026-05-05T10:01:00-07:00",
        )
        entries = load_camera_pending(tmp_path)
        assert len(entries) == 2
        assert entries[0]["filename"] == "X.CR3"
        assert entries[1]["filename"] == "Y.CR3"

    def test_remove_drops_matching_entry(self, tmp_path):
        save_camera_pending(tmp_path, [
            {"folder": "/a", "filename": "X.CR3", "captured_at": "t1"},
            {"folder": "/a", "filename": "Y.CR3", "captured_at": "t2"},
        ])
        remove_camera_pending(tmp_path, CameraFileRef(folder="/a", filename="X.CR3"))
        entries = load_camera_pending(tmp_path)
        assert len(entries) == 1
        assert entries[0]["filename"] == "Y.CR3"

    def test_remove_is_idempotent_when_entry_missing(self, tmp_path):
        save_camera_pending(tmp_path, [
            {"folder": "/a", "filename": "X.CR3", "captured_at": "t1"},
        ])
        remove_camera_pending(tmp_path, CameraFileRef(folder="/a", filename="ZZZ.CR3"))
        assert len(load_camera_pending(tmp_path)) == 1

    def test_save_uses_atomic_write(self, tmp_path):
        save_camera_pending(tmp_path, [{"folder": "/a", "filename": "X.CR3", "captured_at": "t"}])
        save_camera_pending(tmp_path, [{"folder": "/a", "filename": "Y.CR3", "captured_at": "t"}])
        leftovers = list(tmp_path.glob("*.tmp"))
        assert leftovers == []


from pathlib import Path

from timelapse_agent import (
    GPHOTO2_STAGE_DIR,
    gphoto2_download_file,
)


class TestGphoto2DownloadFile:
    def test_calls_gphoto2_with_correct_args(self, tmp_path):
        ref = CameraFileRef(folder="/store_0001/DCIM/100CANON", filename="IMG_42.CR3")
        dest = tmp_path / "IMG_42.CR3"
        # Simulate gphoto2 creating the file as a side effect.
        def fake_run(cmd, **kwargs):
            dest.write_bytes(b"fake-image-bytes")
            return MagicMock(returncode=0, stdout="", stderr="")
        with patch("timelapse_agent.subprocess.run", side_effect=fake_run) as mock_run:
            result = gphoto2_download_file(ref, dest)
        assert result == dest
        assert dest.exists()
        cmd_args = mock_run.call_args[0][0]
        assert cmd_args[0] == "gphoto2"
        assert "--folder" in cmd_args
        assert "/store_0001/DCIM/100CANON" in cmd_args
        assert "--filename" in cmd_args
        assert str(dest) in cmd_args
        assert "--get-file" in cmd_args
        assert "IMG_42.CR3" in cmd_args
        assert "--force-overwrite" in cmd_args

    def test_creates_parent_directory(self, tmp_path):
        ref = CameraFileRef(folder="/a", filename="X.CR3")
        dest = tmp_path / "subdir" / "X.CR3"
        def fake_run(cmd, **kwargs):
            dest.write_bytes(b"x")
            return MagicMock(returncode=0, stdout="", stderr="")
        with patch("timelapse_agent.subprocess.run", side_effect=fake_run):
            gphoto2_download_file(ref, dest)
        assert dest.exists()

    def test_default_stage_dir_is_tmpfs_path(self):
        # Documents the tmpfs choice — /tmp is RAM-backed on Pi OS.
        assert GPHOTO2_STAGE_DIR == Path("/tmp/timelapse-agent-stage")


from timelapse_agent import gphoto2_delete_file


class TestGphoto2DeleteFile:
    def test_calls_gphoto2_with_correct_args(self):
        ref = CameraFileRef(folder="/a/b", filename="IMG_1.CR3")
        completed = MagicMock(returncode=0, stdout="", stderr="")
        with patch("timelapse_agent.subprocess.run", return_value=completed) as mock_run:
            gphoto2_delete_file(ref)
        cmd_args = mock_run.call_args[0][0]
        assert cmd_args[0] == "gphoto2"
        assert "--folder" in cmd_args
        assert "/a/b" in cmd_args
        assert "--delete-file" in cmd_args
        assert "IMG_1.CR3" in cmd_args

    def test_swallows_file_not_found_on_camera(self):
        # If the file is already gone (e.g. user deleted it on the camera, or
        # we previously deleted it but crashed before removing the state entry),
        # treat that as success — the queue entry should be cleared either way.
        err = subprocess.CalledProcessError(
            returncode=1, cmd="gphoto2",
            stderr="ERROR: File not found.\n",
        )
        with patch("timelapse_agent.subprocess.run", side_effect=err):
            # Should NOT raise.
            gphoto2_delete_file(CameraFileRef(folder="/a", filename="GONE.CR3"))

    def test_propagates_other_errors(self):
        err = subprocess.CalledProcessError(
            returncode=1, cmd="gphoto2",
            stderr="ERROR: Could not claim USB device.\n",
        )
        with patch("timelapse_agent.subprocess.run", side_effect=err):
            with pytest.raises(subprocess.CalledProcessError):
                gphoto2_delete_file(CameraFileRef(folder="/a", filename="X.CR3"))


from timelapse_agent import capture_frame


class TestCaptureFrameGphoto2Branch:
    def test_gphoto2_capture_appends_to_queue_and_returns_ref(self, tmp_path):
        config = {"camera_backend": "gphoto2"}
        completed = MagicMock(
            returncode=0,
            stdout="New file is in location /a/b/IMG_99.CR3 on the camera\n",
            stderr="",
        )
        with patch("timelapse_agent.resolve_active_backend", return_value="gphoto2"), \
             patch("timelapse_agent.subprocess.run", return_value=completed):
            result = capture_frame(tmp_path, config)
        assert isinstance(result, CameraFileRef)
        assert result.filename == "IMG_99.CR3"
        entries = load_camera_pending(tmp_path)
        assert len(entries) == 1
        assert entries[0]["filename"] == "IMG_99.CR3"
        assert entries[0]["folder"] == "/a/b"
        assert "captured_at" in entries[0]

    def test_rpicam_capture_path_unchanged(self, tmp_path):
        # When backend is rpicam, capture_frame should still write to pending/.
        config = {"camera_backend": "rpicam", "jpeg_quality": 85}

        def fake_run(cmd, **kwargs):
            # Simulate rpicam writing the temp jpg.
            output_arg = cmd[cmd.index("--output") + 1] if "--output" in cmd else cmd[cmd.index("-o") + 1]
            Path(output_arg).write_bytes(b"jpeg-bytes")
            return MagicMock(returncode=0)

        with patch("timelapse_agent.resolve_active_backend", return_value="rpicam"), \
             patch("timelapse_agent.find_capture_command", return_value="/usr/bin/rpicam-still"), \
             patch("timelapse_agent.subprocess.run", side_effect=fake_run):
            result = capture_frame(tmp_path, config)
        assert isinstance(result, Path)
        assert result.suffix == ".jpg"
        assert result.parent == tmp_path / "pending"

    def test_no_backend_raises(self, tmp_path):
        with patch("timelapse_agent.resolve_active_backend", return_value=None):
            with pytest.raises(RuntimeError, match="No camera backend"):
                capture_frame(tmp_path, {"camera_backend": "auto"})
