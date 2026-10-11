import unittest
from decimal import Decimal

from fastapi import HTTPException

from app.utils import month_bounds, ranked_late_rows, round_report, trend_days, working_days_between


class PhaseThreeAnalyticsTests(unittest.TestCase):
    def test_month_bounds_validate_calendar_months(self):
        self.assertEqual(month_bounds("2026-02"), ("2026-02-01", "2026-02-28"))

        with self.assertRaises(HTTPException) as exc:
            month_bounds("2026-13")

        self.assertEqual(exc.exception.status_code, 422)

    def test_working_days_start_at_join_date(self):
        self.assertEqual(working_days_between("2026-07-08", "2026-07-01", "2026-07-10"), 3)

    def test_report_rounding_uses_half_up(self):
        self.assertEqual(round_report(Decimal("1.005"), 2), 1.01)
        self.assertEqual(round_report(Decimal("0.33335"), 4), 0.3334)

    def test_late_leaderboard_uses_competition_ranking_and_keeps_cutoff_ties(self):
        ranked = ranked_late_rows(
            [
                {"emp_code": "EMP0001", "total_late_minutes": 50, "late_count": 2},
                {"emp_code": "EMP0002", "total_late_minutes": 40, "late_count": 1},
                {"emp_code": "EMP0003", "total_late_minutes": 40, "late_count": 3},
                {"emp_code": "EMP0004", "total_late_minutes": 30, "late_count": 1},
            ],
            2,
        )

        self.assertEqual([row["rank"] for row in ranked], [1, 2, 2])
        self.assertEqual([row["emp_code"] for row in ranked], ["EMP0001", "EMP0002", "EMP0003"])

    def test_trend_days_limit_range_to_ninety_two_days(self):
        self.assertEqual(len(trend_days("2026-07-01", "2026-07-03")), 3)

        with self.assertRaises(HTTPException) as exc:
            trend_days("2026-01-01", "2026-04-04")

        self.assertEqual(exc.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
