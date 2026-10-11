import unittest

from fastapi import HTTPException

from app.utils import build_explain_command


class PhaseFourExplainTests(unittest.TestCase):
    def test_attendance_list_explain_uses_same_filter_sort_and_pagination(self):
        spec = build_explain_command(
            "attendance_list",
            {
                "emp_code": "EMP0001",
                "date_from": "2026-07-01",
                "date_to": "2026-07-31",
                "status": "PRESENT",
                "page": 2,
                "page_size": 10,
            },
        )

        self.assertEqual(spec["collection"], "attendance_logs")
        self.assertEqual(spec["command"]["find"], "attendance_logs")
        self.assertEqual(spec["command"]["skip"], 10)
        self.assertEqual(spec["command"]["sort"], {"date": -1, "emp_code": 1})
        self.assertEqual(spec["command"]["filter"]["date"], {"$gte": "2026-07-01", "$lte": "2026-07-31"})

    def test_employee_monthly_explain_requires_emp_code_and_month(self):
        with self.assertRaises(HTTPException) as exc:
            build_explain_command("employee_monthly", {"month": "2026-07"})

        self.assertEqual(exc.exception.status_code, 422)

    def test_department_trend_explain_uses_employee_aggregation(self):
        spec = build_explain_command(
            "department_trend",
            {"department": "Engineering", "from": "2026-07-01", "to": "2026-07-03"},
        )

        self.assertEqual(spec["collection"], "employees")
        self.assertEqual(spec["command"]["aggregate"], "employees")
        self.assertIn("pipeline", spec["command"])


if __name__ == "__main__":
    unittest.main()
