"""Business-rule helpers, serializers, and MongoDB aggregation builders."""
import calendar
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

from fastapi import HTTPException
from pymongo import ASCENDING, DESCENDING

from .constants import (
    ALL_STATUSES,
    DATE_RE,
    IST,
    MONTH_RE,
    PRESENCE_STATUS_LIST,
    PRESENCE_STATUSES,
    TIME_RE,
    WEEKDAY_NUMBERS,
)


def validate_date_string(value: str) -> str:
    if not DATE_RE.match(value):
        raise ValueError("date must be YYYY-MM-DD")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("date must be valid") from exc
    return value


def validate_shift_time(value: str) -> str:
    if not TIME_RE.match(value):
        raise ValueError("time must be HH:MM")
    return value


def utc_now_seconds() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def normalize_instant(epoch_ms: Optional[int]) -> datetime:
    if epoch_ms is None:
        return utc_now_seconds()
    return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).replace(microsecond=0)


def to_epoch_millis(value: Optional[datetime]) -> Optional[int]:
    if value is None:
        return None
    utc_value = ensure_utc(value).replace(microsecond=0)
    return int(utc_value.timestamp() * 1000)


def parse_time(value: str) -> time:
    hour, minute = map(int, value.split(":"))
    return time(hour=hour, minute=minute)


def shift_instant(date_str: str, shift_time: str, *, next_day: bool = False) -> datetime:
    shift_date = date.fromisoformat(date_str) + (timedelta(days=1) if next_day else timedelta())
    return datetime.combine(shift_date, parse_time(shift_time), tzinfo=IST).astimezone(timezone.utc)


def is_overnight_shift(shift_start: str, shift_end: str) -> bool:
    return parse_time(shift_end) <= parse_time(shift_start)


def attendance_date_for(punch_in: datetime, shift_start: str, shift_end: str) -> str:
    local = ensure_utc(punch_in).astimezone(IST)
    attendance_day = local.date()
    if is_overnight_shift(shift_start, shift_end) and local.time() < parse_time(shift_end):
        attendance_day -= timedelta(days=1)
    return attendance_day.isoformat()


def floor_minutes(delta_seconds: float) -> int:
    return int(delta_seconds // 60)


def compute_late_minutes(punch_in: datetime, shift_start: str, date_str: str) -> int:
    start = shift_instant(date_str, shift_start)
    seconds = (ensure_utc(punch_in) - start).total_seconds()
    if seconds <= 10 * 60:
        return 0
    return max(0, floor_minutes(seconds))


def round_half_up(value: Decimal, places: str = "0.01") -> Decimal:
    return value.quantize(Decimal(places), rounding=ROUND_HALF_UP)


def round_report(value: Any, places: int = 2) -> float:
    quant = "0." + ("0" * (places - 1)) + "1"
    return float(round_half_up(Decimal(str(value)), quant))


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    seconds = Decimal(str((ensure_utc(punch_out) - ensure_utc(punch_in)).total_seconds()))
    return float(round_half_up(seconds / Decimal("3600"), "0.01"))


def compute_overtime_minutes(punch_out: datetime, shift_end: str, date_str: str, shift_start: str) -> int:
    end = shift_instant(date_str, shift_end, next_day=is_overnight_shift(shift_start, shift_end))
    minutes = max(0, floor_minutes((ensure_utc(punch_out) - end).total_seconds()))
    return minutes if minutes >= 30 else 0


def validate_punch_window(punch_in: datetime, punch_out: Optional[datetime]) -> None:
    if punch_out is None:
        return
    seconds = (ensure_utc(punch_out) - ensure_utc(punch_in)).total_seconds()
    if seconds <= 0:
        raise HTTPException(422, "punch_out must be after punch_in")
    if seconds > 24 * 60 * 60:
        raise HTTPException(422, "punch_out must be within 24 hours of punch_in")


def normalize_attendance_base(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "emp_code": doc["emp_code"],
        "date": doc["date"],
        "status": doc["status"],
        "punch_in": doc.get("punch_in"),
        "punch_out": doc.get("punch_out"),
        "work_hours": doc.get("work_hours"),
        "late_minutes": doc.get("late_minutes", 0),
        "overtime_minutes": doc.get("overtime_minutes", 0),
        "half_day": doc.get("half_day", False),
    }


def clear_absence_fields(values: dict[str, Any]) -> dict[str, Any]:
    values.update(
        {
            "punch_in": None,
            "punch_out": None,
            "work_hours": None,
            "late_minutes": 0,
            "overtime_minutes": 0,
            "half_day": False,
        }
    )
    return values


def recompute_presence_fields(values: dict[str, Any], emp: dict[str, Any]) -> dict[str, Any]:
    punch_in = values.get("punch_in")
    if punch_in is None:
        raise HTTPException(422, "presence status requires punch_in")

    punch_in = ensure_utc(punch_in).replace(microsecond=0)
    punch_out = values.get("punch_out")
    punch_out = ensure_utc(punch_out).replace(microsecond=0) if punch_out is not None else None

    # R1: a corrected punch-in must still belong to this record's IST attendance date.
    if attendance_date_for(punch_in, emp["shift_start"], emp["shift_end"]) != values["date"]:
        raise HTTPException(422, "punch_in must stay on the record attendance date")

    validate_punch_window(punch_in, punch_out)
    values["punch_in"] = punch_in
    values["punch_out"] = punch_out
    values["late_minutes"] = compute_late_minutes(punch_in, emp["shift_start"], values["date"])

    if punch_out is None:
        values["work_hours"] = None
        values["overtime_minutes"] = 0
        values["half_day"] = False
    else:
        work_hours = compute_work_hours(punch_in, punch_out)
        values["work_hours"] = work_hours
        values["overtime_minutes"] = compute_overtime_minutes(
            punch_out,
            emp["shift_end"],
            values["date"],
            emp["shift_start"],
        )
        values["half_day"] = work_hours < 4.5
    return values


def finalize_attendance_values(record: dict[str, Any], emp: dict[str, Any]) -> dict[str, Any]:
    status = record["status"]
    if status not in ALL_STATUSES:
        raise HTTPException(422, "invalid status")

    values = normalize_attendance_base(record)
    values["status"] = status
    if status in {"ABSENT", "LEAVE"}:
        return clear_absence_fields(values)
    return recompute_presence_fields(values, emp)


def values_match(left: Any, right: Any) -> bool:
    if isinstance(left, datetime) or isinstance(right, datetime):
        return to_epoch_millis(left) == to_epoch_millis(right)
    return left == right


def build_regularization_update(
    existing: dict[str, Any],
    emp: dict[str, Any],
    requested: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if requested.get("status") in {"ABSENT", "LEAVE"} and (
        "punch_in" in requested or "punch_out" in requested
    ):
        raise HTTPException(422, "ABSENT or LEAVE cannot include punch times")

    candidate = normalize_attendance_base(existing)
    for field in ("status", "punch_in", "punch_out"):
        if field in requested:
            candidate[field] = requested[field]

    update = finalize_attendance_values(candidate, emp)
    old_values = normalize_attendance_base(existing)
    changes = {}
    for field in ("status", "punch_in", "punch_out", "work_hours", "late_minutes", "overtime_minutes", "half_day"):
        if not values_match(old_values.get(field), update.get(field)):
            changes[field] = {"from": old_values.get(field), "to": update.get(field)}

    if not changes:
        raise HTTPException(422, "request changes nothing")
    history = {
        "at": utc_now_seconds(),
        "by": requested["regularized_by"],
        "reason": requested["reason"],
        "changes": changes,
    }
    return update, history


def normalize_history(history: Optional[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    output = []
    for item in history or []:
        copied = dict(item)
        copied["at"] = to_epoch_millis(copied.get("at"))
        changes = {}
        for key, change in (copied.get("changes") or {}).items():
            converted = dict(change)
            if key in {"punch_in", "punch_out"}:
                converted["from"] = to_epoch_millis(converted.get("from"))
                converted["to"] = to_epoch_millis(converted.get("to"))
            changes[key] = converted
        copied["changes"] = changes
        output.append(copied)
    return output


def serialize_employee(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "emp_code": doc["emp_code"],
        "name": doc["name"],
        "email": doc["email"],
        "department": doc["department"],
        "shift_start": doc.get("shift_start", "09:30"),
        "shift_end": doc.get("shift_end", "18:30"),
        "joined_on": doc["joined_on"],
        "created_at": to_epoch_millis(doc["created_at"]),
    }


def serialize_attendance(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "emp_code": doc["emp_code"],
        "date": doc["date"],
        "status": doc["status"],
        "punch_in": to_epoch_millis(doc.get("punch_in")),
        "punch_out": to_epoch_millis(doc.get("punch_out")),
        "work_hours": doc.get("work_hours"),
        "late_minutes": doc.get("late_minutes", 0),
        "overtime_minutes": doc.get("overtime_minutes", 0),
        "half_day": doc.get("half_day", False),
        "history": normalize_history(doc.get("history", [])),
    }


def build_attendance_query(
    emp_code: Optional[str],
    date_from: Optional[str],
    date_to: Optional[str],
    status: Optional[str],
) -> dict[str, Any]:
    if date_from:
        validate_date_string(date_from)
    if date_to:
        validate_date_string(date_to)
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from must be before or equal to date_to")
    query: dict[str, Any] = {}
    if emp_code:
        query["emp_code"] = emp_code
    if date_from or date_to:
        query["date"] = {}
        if date_from:
            query["date"]["$gte"] = date_from
        if date_to:
            query["date"]["$lte"] = date_to
    if status:
        query["status"] = status
    return query


def month_bounds(month: str) -> tuple[str, str]:
    if not MONTH_RE.match(month):
        raise HTTPException(422, "month must be YYYY-MM")
    year, month_num = map(int, month.split("-"))
    last_day = calendar.monthrange(year, month_num)[1]
    return f"{month}-01", f"{month}-{last_day:02d}"


def working_days_between(joined_on: str, start: str, end: str) -> int:
    current = max(date.fromisoformat(joined_on), date.fromisoformat(start))
    last = date.fromisoformat(end)
    total = 0
    while current <= last:
        if current.weekday() < 5:
            total += 1
        current += timedelta(days=1)
    return total


def trend_days(start: str, end: str) -> list[str]:
    validate_date_string(start)
    validate_date_string(end)
    first = date.fromisoformat(start)
    last = date.fromisoformat(end)
    if last < first:
        raise HTTPException(422, "to must be after or equal to from")
    if (last - first).days + 1 > 92:
        raise HTTPException(422, "date range cannot exceed 92 days")
    days = []
    current = first
    while current <= last:
        days.append(current.isoformat())
        current += timedelta(days=1)
    return days


def date_string_expr(field: str) -> dict[str, Any]:
    return {"$dateToString": {"format": "%Y-%m-%d", "date": field}}


def weekday_expr(date_field: str) -> dict[str, Any]:
    return {"$in": [{"$dayOfWeek": {"$dateFromString": {"dateString": date_field}}}, WEEKDAY_NUMBERS]}


def present_day_expr(status_field: str, half_day_field: str, *, working_day: Optional[Any] = None) -> dict[str, Any]:
    conditions: list[Any] = [{"$in": [status_field, PRESENCE_STATUS_LIST]}]
    if working_day is not None:
        conditions.append(working_day)
    return {"$cond": [{"$and": conditions}, {"$cond": [{"$ifNull": [half_day_field, False]}, 0.5, 1]}, 0]}


def present_completed_work_expr(status_field: str, work_hours_field: str) -> dict[str, Any]:
    return {
        "$and": [
            {"$in": [status_field, PRESENCE_STATUS_LIST]},
            {"$ne": [work_hours_field, None]},
        ]
    }


def count_if(condition: Any) -> dict[str, Any]:
    return {"$sum": {"$cond": [condition, 1, 0]}}


def sum_minutes(field: str) -> dict[str, Any]:
    return {"$sum": {"$ifNull": [field, 0]}}


def aggregate_one(collection: Any, pipeline: list[dict[str, Any]]) -> dict[str, Any]:
    return next(iter(collection.aggregate(pipeline)), {})


def aggregate_command(collection: str, pipeline: list[dict[str, Any]]) -> dict[str, Any]:
    return {"aggregate": collection, "pipeline": pipeline, "cursor": {}}


def ranked_late_rows(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    ranked = []
    previous_minutes = None
    current_rank = 0
    for index, row in enumerate(rows, start=1):
        if row["total_late_minutes"] != previous_minutes:
            current_rank = index
            previous_minutes = row["total_late_minutes"]
        if current_rank > limit:
            break
        ranked.append({"rank": current_rank, **row})
    return ranked


def employee_monthly_pipeline(emp_code: str, start: str, end: str) -> list[dict[str, Any]]:
    has_late_minutes = {"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}
    return [
        {"$match": {"emp_code": emp_code, "date": {"$gte": start, "$lte": end}}},
        {
            "$group": {
                "_id": None,
                "present_days": {
                    "$sum": present_day_expr("$status", "$half_day", working_day=weekday_expr("$date")),
                },
                "leave_days": count_if({"$eq": ["$status", "LEAVE"]}),
                "late_count": count_if(has_late_minutes),
                "total_late_minutes": sum_minutes("$late_minutes"),
                "total_overtime_minutes": sum_minutes("$overtime_minutes"),
            }
        },
    ]


def department_summary_pipeline(month: str, department: Optional[str]) -> list[dict[str, Any]]:
    start, end = month_bounds(month)
    employee_match: dict[str, Any] = {"joined_on": {"$lte": end}}
    if department:
        employee_match["department"] = department
    completed_work = present_completed_work_expr("$logs.status", "$logs.work_hours")
    has_late_minutes = {"$gt": [{"$ifNull": ["$logs.late_minutes", 0]}, 0]}
    return [
        {"$match": employee_match},
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"emp": "$emp_code"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$emp_code", "$$emp"]},
                                    {"$gte": ["$date", start]},
                                    {"$lte": ["$date", end]},
                                ]
                            }
                        }
                    },
                    {"$addFields": {"is_working_day": weekday_expr("$date")}},
                ],
                "as": "logs",
            }
        },
        {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}},
        {
            "$group": {
                "_id": {"department": "$department", "emp_code": "$emp_code"},
                "present_days": {
                    "$sum": present_day_expr("$logs.status", "$logs.half_day", working_day="$logs.is_working_day"),
                },
                "total_work_hours": {"$sum": {"$cond": [completed_work, "$logs.work_hours", 0]}},
                "work_hours_count": count_if(completed_work),
                "late_count": count_if(has_late_minutes),
                "total_late_minutes": sum_minutes("$logs.late_minutes"),
                "leave_count": count_if({"$eq": ["$logs.status", "LEAVE"]}),
                "on_duty_count": count_if({"$eq": ["$logs.status", "ON_DUTY"]}),
            }
        },
        {
            "$group": {
                "_id": "$_id.department",
                "headcount": {"$sum": 1},
                "present_days": {"$sum": "$present_days"},
                "total_work_hours": {"$sum": "$total_work_hours"},
                "work_hours_count": {"$sum": "$work_hours_count"},
                "late_count": {"$sum": "$late_count"},
                "total_late_minutes": {"$sum": "$total_late_minutes"},
                "leave_count": {"$sum": "$leave_count"},
                "on_duty_count": {"$sum": "$on_duty_count"},
            }
        },
        {"$sort": {"_id": ASCENDING}},
    ]


def late_leaderboard_pipeline(month: str, department: Optional[str]) -> list[dict[str, Any]]:
    start, end = month_bounds(month)
    pipeline: list[dict[str, Any]] = [
        {"$match": {"date": {"$gte": start, "$lte": end}, "late_minutes": {"$gt": 0}}},
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"},
                "late_count": {"$sum": 1},
            }
        },
        {"$sort": {"total_late_minutes": DESCENDING, "_id": ASCENDING}},
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "employee",
            }
        },
        {"$unwind": "$employee"},
    ]
    if department:
        pipeline.append({"$match": {"employee.department": department}})
    pipeline.extend(
        [
            {
                "$project": {
                    "_id": 0,
                    "emp_code": "$_id",
                    "name": "$employee.name",
                    "department": "$employee.department",
                    "total_late_minutes": 1,
                    "late_count": 1,
                }
            },
            {"$sort": {"total_late_minutes": DESCENDING, "emp_code": ASCENDING}},
        ]
    )
    return pipeline


def department_trend_pipeline(department: str, start: str, end: str) -> list[dict[str, Any]]:
    day_count = len(trend_days(start, end))
    start_dt = datetime.combine(date.fromisoformat(start), time.min, tzinfo=timezone.utc)
    has_late_minutes = {"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}
    return [
        {"$match": {"department": department}},
        {"$limit": 1},
        {
            "$project": {
                "_id": 0,
                "date_obj": {
                    "$map": {
                        "input": {"$range": [0, day_count]},
                        "as": "offset",
                        "in": {"$dateAdd": {"startDate": start_dt, "unit": "day", "amount": "$$offset"}},
                    }
                },
            }
        },
        {"$unwind": "$date_obj"},
        {
            "$set": {
                "date": date_string_expr("$date_obj"),
                "is_working_day": {"$in": [{"$dayOfWeek": "$date_obj"}, WEEKDAY_NUMBERS]},
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "let": {"dept": department, "day": "$date"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$department", "$$dept"]},
                                    {"$lte": ["$joined_on", "$$day"]},
                                ]
                            }
                        }
                    },
                    {"$count": "count"},
                ],
                "as": "headcount_rows",
            }
        },
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"dept": department, "day": "$date"},
                "pipeline": [
                    {"$match": {"$expr": {"$eq": ["$date", "$$day"]}}},
                    {
                        "$lookup": {
                            "from": "employees",
                            "localField": "emp_code",
                            "foreignField": "emp_code",
                            "as": "employee",
                        }
                    },
                    {"$unwind": "$employee"},
                    {"$match": {"$expr": {"$eq": ["$employee.department", "$$dept"]}}},
                    {
                        "$group": {
                            "_id": None,
                            "present_count": {"$sum": present_day_expr("$status", "$half_day")},
                            "late_count": count_if(has_late_minutes),
                        }
                    },
                ],
                "as": "log_rows",
            }
        },
        {
            "$set": {
                "headcount": {"$ifNull": [{"$first": "$headcount_rows.count"}, 0]},
                "present_count": {"$ifNull": [{"$first": "$log_rows.present_count"}, 0]},
                "late_count": {"$ifNull": [{"$first": "$log_rows.late_count"}, 0]},
            }
        },
        {
            "$set": {
                "attendance_rate": {
                    "$cond": [
                        {"$and": ["$is_working_day", {"$gt": ["$headcount", 0]}]},
                        {"$divide": ["$present_count", "$headcount"]},
                        None,
                    ]
                }
            }
        },
        {
            "$setWindowFields": {
                "sortBy": {"date_obj": ASCENDING},
                "output": {
                    "moving_avg_7d": {
                        "$avg": "$attendance_rate",
                        "window": {"documents": [-6, 0]},
                    }
                },
            }
        },
        {"$sort": {"date": ASCENDING}},
        {
            "$project": {
                "_id": 0,
                "date": 1,
                "is_working_day": 1,
                "headcount": 1,
                "present_count": 1,
                "late_count": 1,
                "attendance_rate": 1,
                "moving_avg_7d": 1,
            }
        },
    ]


def require_explain_param(params: dict[str, Any], name: str) -> Any:
    value = params.get(name)
    if value in (None, ""):
        raise HTTPException(422, f"{name} is required")
    return value


def explain_spec(collection: str, command: dict[str, Any]) -> dict[str, Any]:
    return {"collection": collection, "command": command}


def build_explain_command(endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
    if endpoint == "attendance_list":
        page = int(params.get("page", 1))
        page_size = int(params.get("page_size", 20))
        if page < 1 or page_size < 1 or page_size > 100:
            raise HTTPException(422, "invalid pagination")
        query = build_attendance_query(
            params.get("emp_code"),
            params.get("date_from"),
            params.get("date_to"),
            params.get("status"),
        )
        return explain_spec(
            "attendance_logs",
            {
                "find": "attendance_logs",
                "filter": query,
                "sort": {"date": -1, "emp_code": 1},
                "skip": (page - 1) * page_size,
                "limit": page_size,
            },
        )

    if endpoint == "employee_monthly":
        emp_code = require_explain_param(params, "emp_code")
        month = require_explain_param(params, "month")
        start, end = month_bounds(month)
        return explain_spec(
            "attendance_logs",
            aggregate_command("attendance_logs", employee_monthly_pipeline(emp_code, start, end)),
        )

    if endpoint == "department_summary":
        month = require_explain_param(params, "month")
        return explain_spec(
            "employees",
            aggregate_command("employees", department_summary_pipeline(month, params.get("department"))),
        )

    if endpoint == "late_leaderboard":
        month = require_explain_param(params, "month")
        return explain_spec(
            "attendance_logs",
            aggregate_command("attendance_logs", late_leaderboard_pipeline(month, params.get("department"))),
        )

    if endpoint == "department_trend":
        department = require_explain_param(params, "department")
        start = require_explain_param(params, "from")
        end = require_explain_param(params, "to")
        return explain_spec(
            "employees",
            aggregate_command("employees", department_trend_pipeline(department, start, end)),
        )

    raise HTTPException(422, "unsupported endpoint")
