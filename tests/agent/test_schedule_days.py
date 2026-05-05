from datetime import datetime
from timelapse_agent import is_in_schedule


# Reference dates with known ISO weekdays:
# 2026-05-04 = Mon (1), 2026-05-08 = Fri (5), 2026-05-09 = Sat (6)
MON_NOON = datetime(2026, 5, 4, 12, 0)
FRI_NOON = datetime(2026, 5, 8, 12, 0)
SAT_NOON = datetime(2026, 5, 9, 12, 0)


class TestScheduleDays:
    def test_no_days_means_all_days(self):
        assert is_in_schedule(MON_NOON, capture_hours=None, schedule_days=None)
        assert is_in_schedule(SAT_NOON, capture_hours=None, schedule_days=None)

    def test_weekdays_only(self):
        weekdays = [1, 2, 3, 4, 5]
        assert is_in_schedule(MON_NOON, capture_hours=None, schedule_days=weekdays)
        assert is_in_schedule(FRI_NOON, capture_hours=None, schedule_days=weekdays)
        assert not is_in_schedule(SAT_NOON, capture_hours=None, schedule_days=weekdays)

    def test_empty_days_means_paused(self):
        assert not is_in_schedule(MON_NOON, capture_hours=None, schedule_days=[])

    def test_days_combine_with_hours(self):
        # On a permitted day inside the hour window: yes
        assert is_in_schedule(MON_NOON, capture_hours=[12], schedule_days=[1, 2])
        # On a permitted day OUTSIDE the hour window: no
        assert not is_in_schedule(MON_NOON, capture_hours=[6, 7], schedule_days=[1, 2])
        # On a forbidden day inside the hour window: no
        assert not is_in_schedule(SAT_NOON, capture_hours=[12], schedule_days=[1, 2])
