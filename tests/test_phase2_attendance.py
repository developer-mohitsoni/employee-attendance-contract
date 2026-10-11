import unittest
from datetime import datetime, timezone

from fastapi import HTTPException

from app.main import build_regularization_update, finalize_attendance_values


EMPLOYEE = {
    "emp_code": "EMP0001",
    "shift_start": "09:30",
    "shift_end": "18:30",
}


class PhaseTwoAttendanceTests(unittest.TestCase):
    def test_finalize_presence_with_punch_out_computes_all_derived_fields(self):
        values = finalize_attendance_values(
            {
                "emp_code": "EMP0001",
                "date": "2026-07-06",
                "status": "PRESENT",
                "punch_in": datetime(2026, 7, 6, 4, 35, tzinfo=timezone.utc),
                "punch_out": datetime(2026, 7, 6, 13, 40, tzinfo=timezone.utc),
            },
            EMPLOYEE,
        )

        self.assertEqual(values["work_hours"], 9.08)
        self.assertEqual(values["late_minutes"], 35)
        self.assertEqual(values["overtime_minutes"], 40)
        self.assertFalse(values["half_day"])

    def test_finalize_absent_clears_times_and_derived_fields(self):
        values = finalize_attendance_values(
            {
                "emp_code": "EMP0001",
                "date": "2026-07-06",
                "status": "ABSENT",
                "punch_in": datetime(2026, 7, 6, 4, 35, tzinfo=timezone.utc),
                "punch_out": datetime(2026, 7, 6, 13, 40, tzinfo=timezone.utc),
            },
            EMPLOYEE,
        )

        self.assertIsNone(values["punch_in"])
        self.assertIsNone(values["punch_out"])
        self.assertIsNone(values["work_hours"])
        self.assertEqual(values["late_minutes"], 0)
        self.assertEqual(values["overtime_minutes"], 0)
        self.assertFalse(values["half_day"])

    def test_finalize_rejects_punch_out_more_than_twenty_four_hours_later(self):
        with self.assertRaises(HTTPException) as exc:
            finalize_attendance_values(
                {
                    "emp_code": "EMP0001",
                    "date": "2026-07-06",
                    "status": "PRESENT",
                    "punch_in": datetime(2026, 7, 6, 4, 0, tzinfo=timezone.utc),
                    "punch_out": datetime(2026, 7, 7, 4, 0, 1, tzinfo=timezone.utc),
                },
                EMPLOYEE,
            )

        self.assertEqual(exc.exception.status_code, 422)

    def test_regularization_builds_history_for_actual_changes_only(self):
        existing = {
            "emp_code": "EMP0001",
            "date": "2026-07-06",
            "status": "PRESENT",
            "punch_in": datetime(2026, 7, 6, 4, 35, tzinfo=timezone.utc),
            "punch_out": datetime(2026, 7, 6, 13, 40, tzinfo=timezone.utc),
            "work_hours": 9.08,
            "late_minutes": 35,
            "overtime_minutes": 40,
            "half_day": False,
            "history": [],
        }

        update, history = build_regularization_update(
            existing,
            EMPLOYEE,
            {
                "punch_in": datetime(2026, 7, 6, 3, 58, tzinfo=timezone.utc),
                "reason": "biometric glitch",
                "regularized_by": "hr.admin",
            },
        )

        self.assertEqual(update["late_minutes"], 0)
        self.assertEqual(update["work_hours"], 9.7)
        self.assertEqual(set(history["changes"]), {"punch_in", "work_hours", "late_minutes"})
        self.assertEqual(history["by"], "hr.admin")
        self.assertEqual(history["reason"], "biometric glitch")


if __name__ == "__main__":
    unittest.main()
