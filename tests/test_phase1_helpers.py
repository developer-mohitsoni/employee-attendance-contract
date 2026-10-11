import unittest
from datetime import datetime, timezone

from app.utils import (
    attendance_date_for,
    compute_late_minutes,
    compute_overtime_minutes,
    compute_work_hours,
    normalize_instant,
    to_epoch_millis,
)


class PhaseOneHelperTests(unittest.TestCase):
    def test_epoch_millis_are_truncated_and_round_tripped_as_utc(self):
        instant = normalize_instant(1783312500123)

        self.assertEqual(instant.tzinfo, timezone.utc)
        self.assertEqual(instant.microsecond, 0)
        self.assertEqual(to_epoch_millis(instant), 1783312500000)

    def test_overnight_punch_before_shift_end_belongs_to_previous_day(self):
        punch_in = datetime(2026, 7, 6, 23, 30, tzinfo=timezone.utc)

        self.assertEqual(attendance_date_for(punch_in, "22:00", "06:00"), "2026-07-06")

    def test_late_minutes_use_strict_grace_boundary(self):
        at_grace = datetime(2026, 7, 6, 4, 10, tzinfo=timezone.utc)
        just_after_grace = datetime(2026, 7, 6, 4, 10, 1, tzinfo=timezone.utc)

        self.assertEqual(compute_late_minutes(at_grace, "09:30", "2026-07-06"), 0)
        self.assertEqual(compute_late_minutes(just_after_grace, "09:30", "2026-07-06"), 10)

    def test_overtime_requires_at_least_thirty_whole_minutes(self):
        twenty_nine = datetime(2026, 7, 6, 13, 29, 59, tzinfo=timezone.utc)
        thirty = datetime(2026, 7, 6, 13, 30, tzinfo=timezone.utc)

        self.assertEqual(compute_overtime_minutes(twenty_nine, "18:30", "2026-07-06", "09:30"), 0)
        self.assertEqual(compute_overtime_minutes(thirty, "18:30", "2026-07-06", "09:30"), 30)

    def test_work_hours_rounds_half_up(self):
        start = datetime(2026, 7, 6, 3, 30, tzinfo=timezone.utc)
        end = datetime(2026, 7, 6, 4, 30, 18, tzinfo=timezone.utc)

        self.assertEqual(compute_work_hours(start, end), 1.01)


if __name__ == "__main__":
    unittest.main()
