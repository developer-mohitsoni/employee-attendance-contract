"""Analytics endpoints backed by MongoDB aggregation pipelines."""
from decimal import Decimal
from typing import Annotated, Optional

from fastapi import APIRouter, HTTPException, Query

from ..database import db
from ..utils import (
    aggregate_one,
    department_summary_pipeline,
    department_trend_pipeline,
    employee_monthly_pipeline,
    late_leaderboard_pipeline,
    month_bounds,
    ranked_late_rows,
    round_report,
    trend_days,
    working_days_between,
)

router = APIRouter()


@router.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$")):
    start, end = month_bounds(month)
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(404, "employee not found")
    working_days = working_days_between(emp["joined_on"], start, end)
    summary = aggregate_one(db.attendance_logs, employee_monthly_pipeline(emp_code, start, end))
    present_days = round_report(summary.get("present_days", 0), 2)
    attendance_pct = None
    if working_days:
        attendance_pct = round_report(Decimal(str(present_days)) * Decimal("100") / Decimal(working_days), 2)
    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": int(summary.get("leave_days", 0)),
        "late_count": int(summary.get("late_count", 0)),
        "total_late_minutes": int(summary.get("total_late_minutes", 0)),
        "total_overtime_minutes": int(summary.get("total_overtime_minutes", 0)),
        "attendance_pct": attendance_pct,
    }


@router.get("/analytics/departments/summary")
def department_summary(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    department: Optional[str] = None,
):
    items = []
    for row in db.employees.aggregate(department_summary_pipeline(month, department)):
        avg_work_hours = None
        if row.get("work_hours_count", 0):
            avg_work_hours = round_report(
                Decimal(str(row["total_work_hours"])) / Decimal(row["work_hours_count"]),
                2,
            )
        items.append(
            {
                "department": row["_id"],
                "headcount": int(row["headcount"]),
                "present_days": round_report(row.get("present_days", 0), 2),
                "avg_work_hours": avg_work_hours,
                "late_count": int(row.get("late_count", 0)),
                "total_late_minutes": int(row.get("total_late_minutes", 0)),
                "leave_count": int(row.get("leave_count", 0)),
                "on_duty_count": int(row.get("on_duty_count", 0)),
            }
        )
    return {"month": month, "items": items}


@router.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    department: Optional[str] = None,
):
    rows = list(db.attendance_logs.aggregate(late_leaderboard_pipeline(month, department)))
    return {"month": month, "items": ranked_late_rows(rows, limit)}


@router.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    from_date: str = Query(..., alias="from"),
    to: str = Query(...),
):
    trend_days(from_date, to)
    if db.employees.count_documents({"department": department}, limit=1) == 0:
        raise HTTPException(404, "department not found")
    rows = []
    for row in db.employees.aggregate(department_trend_pipeline(department, from_date, to)):
        rows.append(
            {
                "date": row["date"],
                "is_working_day": row["is_working_day"],
                "headcount": int(row["headcount"]),
                "present_count": round_report(row.get("present_count", 0), 2),
                "late_count": int(row.get("late_count", 0)),
                "attendance_rate": (
                    None if row.get("attendance_rate") is None else round_report(row["attendance_rate"], 4)
                ),
                "moving_avg_7d": (
                    None if row.get("moving_avg_7d") is None else round_report(row["moving_avg_7d"], 4)
                ),
            }
        )
    return {"department": department, "items": rows}
