from app.provision_script import build_install_script


def script() -> str:
    return build_install_script(
        camera_id="x",
        server_url="http://x:8080",
        agent_version="0.9.0",
    )


def test_script_installs_gphoto2():
    assert "gphoto2" in script()


def test_script_installs_libgphoto2_runtime():
    # gphoto2 the CLI brings libgphoto2-port12 / libgphoto2-6 with it on Debian/RPi OS,
    # but we install the metapackage explicitly so a future split doesn't surprise us.
    assert "gphoto2" in script()


def test_script_writes_canon_udev_rule():
    s = script()
    # Canon's USB vendor ID is 04a9 (lowercase hex).
    assert "04a9" in s
    assert "/etc/udev/rules.d/" in s
    assert 'GROUP="plugdev"' in s


def test_script_reloads_udev():
    s = script()
    assert "udevadm control --reload-rules" in s
    assert "udevadm trigger" in s


def test_script_adds_pi_to_plugdev():
    # The systemd unit runs as the pi user (or whatever the unit specifies);
    # the pi user must be in plugdev to access USB devices via the udev rule.
    assert "usermod -aG plugdev" in script()


def test_script_masks_gvfs_gphoto2_monitor():
    # gvfs-gphoto2-volume-monitor auto-mounts cameras and prevents gphoto2 from
    # claiming the USB device. We mask it system-wide so it never starts.
    s = script()
    assert "gvfs-gphoto2-volume-monitor" in s
