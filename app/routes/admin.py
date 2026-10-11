"""Admin/debug endpoints."""
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Query

from ..database import db
from ..schemas import Page, PageSize
from ..utils import build_explain_command

router = APIRouter()


@router.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: Literal["attendance_list", "employee_monthly", "department_summary", "late_leaderboard", "department_trend"],
    emp_code: Optional[str] = None,
    month: Optional[str] = Query(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    department: Optional[str] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]] = None,
    from_date: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = None,
    page: Page = 1,
    page_size: PageSize = 20,
):
    spec = build_explain_command(
        endpoint,
        {
            "emp_code": emp_code,
            "month": month,
            "department": department,
            "limit": limit,
            "date_from": date_from,
            "date_to": date_to,
            "status": status,
            "from": from_date,
            "to": to,
            "page": page,
            "page_size": page_size,
        },
    )
    explain = db.command("explain", spec["command"], verbosity="executionStats")
    return {"endpoint": endpoint, "collection": spec["collection"], "explain": explain}
