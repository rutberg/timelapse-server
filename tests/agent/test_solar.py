from datetime import date
from timelapse_agent import solar_window, daylight_capture_hours


class TestSolarWindow:
    """Reference values from NOAA for known city/dates (rounded to nearest hour)."""

    def test_stockholm_summer_solstice(self):
        # Stockholm 2026-06-21 — sunrise ~03:31, sunset ~22:08 local; UTC offset +2.
        # Using ceil for sunset (hour is inclusive if sun sets during it), so
        # ceil(22.13) = 23. The exclusive upper bound is 23.
        sunrise, sunset = solar_window(date(2026, 6, 21), 59.33, 18.07, utc_offset_hours=2.0)
        assert 3 <= sunrise <= 4
        assert 22 <= sunset <= 23

    def test_stockholm_winter_solstice(self):
        # 2026-12-21 — sunrise ~08:43, sunset ~14:48 local; UTC offset +1.
        sunrise, sunset = solar_window(date(2026, 12, 21), 59.33, 18.07, utc_offset_hours=1.0)
        assert 8 <= sunrise <= 9
        assert 14 <= sunset <= 15

    def test_equator_equinox(self):
        # Quito (~0,-78), 2026-03-20 — sunrise/sunset roughly 06:00/18:00 local (UTC-5).
        sunrise, sunset = solar_window(date(2026, 3, 20), 0.0, -78.5, utc_offset_hours=-5.0)
        assert 5 <= sunrise <= 7
        assert 17 <= sunset <= 19

    def test_polar_night_returns_empty(self):
        # 80°N in winter — sun never rises.
        result = solar_window(date(2026, 12, 21), 80.0, 0.0, utc_offset_hours=0.0)
        assert result is None

    def test_polar_day_returns_full(self):
        # 80°N in summer — sun never sets.
        result = solar_window(date(2026, 6, 21), 80.0, 0.0, utc_offset_hours=0.0)
        assert result == (0, 24)


class TestDaylightCaptureHours:
    def test_with_location(self):
        # Stockholm-ish in May — a wide window, certainly including noon and excluding 02:00.
        hours = daylight_capture_hours(
            today=date(2026, 5, 4), latitude=59.33, longitude=18.07, utc_offset_hours=2.0,
        )
        assert 12 in hours
        assert 2 not in hours

    def test_without_location_falls_back(self):
        # No lat/lon → fixed 06:00–20:00 fallback.
        hours = daylight_capture_hours(today=date(2026, 5, 4), latitude=None, longitude=None)
        assert hours == list(range(6, 20))

    def test_polar_night_returns_empty_list(self):
        hours = daylight_capture_hours(
            today=date(2026, 12, 21), latitude=80.0, longitude=0.0, utc_offset_hours=0.0,
        )
        assert hours == []
