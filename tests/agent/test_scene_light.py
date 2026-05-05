from timelapse_agent import (
    mean_y_from_yuv,
    should_capture_for_scene,
)


class TestMeanYFromYuv:
    def test_uniform_dark(self):
        # 64*48 = 3072 bytes, all zero (Y plane)
        raw = bytes([0]) * (64 * 48)
        # Trailing UV planes don't affect Y mean
        raw += bytes([128]) * (64 * 48 // 2)
        assert mean_y_from_yuv(raw, width=64, height=48) == 0

    def test_uniform_bright(self):
        raw = bytes([255]) * (64 * 48) + bytes([128]) * (64 * 48 // 2)
        assert mean_y_from_yuv(raw, width=64, height=48) == 255

    def test_mid_grey(self):
        raw = bytes([128]) * (64 * 48) + bytes([128]) * (64 * 48 // 2)
        assert mean_y_from_yuv(raw, width=64, height=48) == 128

    def test_short_buffer_returns_none(self):
        assert mean_y_from_yuv(b"abc", width=64, height=48) is None


class TestSceneCaptureDecision:
    def test_above_threshold_captures(self):
        assert should_capture_for_scene(current_light=120, threshold=60) is True

    def test_below_threshold_skips(self):
        assert should_capture_for_scene(current_light=20, threshold=60) is False

    def test_equal_to_threshold_captures(self):
        assert should_capture_for_scene(current_light=60, threshold=60) is True

    def test_no_threshold_means_capture(self):
        assert should_capture_for_scene(current_light=20, threshold=None) is True

    def test_no_reading_means_capture(self):
        # Conservative: if we couldn't sample, don't gate the user out
        assert should_capture_for_scene(current_light=None, threshold=60) is True
