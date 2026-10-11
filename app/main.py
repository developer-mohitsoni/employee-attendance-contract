"""Employee Attendance & Analytics API."""
import os
import re
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Annotated, Any, Literal, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pymongo import ASCENDING, DESCENDING, MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError

load_dotenv()

client = MongoClient(
    os.getenv("MONGO_URI", "mongodb://localhost:27017"),
    tz_aware=True,
    tzinfo=timezone.utc,
)
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))
EPOCH_MIN = 100_000_000_000
EPOCH_MAX = 4_102_444_800_000
PRESENCE_STATUSES = {"PRESENT", "WFH", "ON_DUTY"}
ALL_STATUSES = PRESENCE_STATUSES | {"ABSENT", "LEAVE"}
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

EpochMillis = Annotated[int, Field(strict=True, ge=EPOCH_MIN, le=EPOCH_MAX)]
Page = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def ensure_indexes() -> None:
    db.employees.create_index([("emp_code", ASCENDING)], unique=True, name="uniq_employees_emp_code")
    db.employees.create_index([("department", ASCENDING), ("emp_code", ASCENDING)], name="employees_department_emp_code")
    db.employees.create_index([("department", ASCENDING), ("joined_on", ASCENDING)], name="employees_department_joined_on")
    db.attendance_logs.create_index(
        [("emp_code", ASCENDING), ("date", ASCENDING)],
        unique=True,
        name="uniq_attendance_emp_code_date",
    )
    db.attendance_logs.create_index(
        [("date", DESCENDING), ("emp_code", ASCENDING), ("status", ASCENDING)],
        name="attendance_date_emp_status",
    )
    db.attendance_logs.create_index(
        [("emp_code", ASCENDING), ("punch_in", DESCENDING)],
        name="attendance_emp_punch_in",
    )


@app.on_event("startup")
def on_startup() -> None:
    ensure_indexes()


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


def ist_midnight(date_str: str) -> datetime:
    return datetime.combine(date.fromisoformat(date_str), time.min, tzinfo=IST)


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


def finalize_attendance_values(record: dict[str, Any], emp: dict[str, Any]) -> dict[str, Any]:
    status = record["status"]
    if status not in ALL_STATUSES:
        raise HTTPException(422, "invalid status")
    values = normalize_attendance_base(record)
    values["status"] = status
    if status not in PRESENCE_STATUSES:
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

    punch_in = values.get("punch_in")
    if punch_in is None:
        raise HTTPException(422, "presence status requires punch_in")
    punch_in = ensure_utc(punch_in).replace(microsecond=0)
    punch_out = values.get("punch_out")
    punch_out = ensure_utc(punch_out).replace(microsecond=0) if punch_out is not None else None
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


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    department: str = Field(min_length=1, max_length=50)
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str

    @field_validator("shift_start", "shift_end")
    @classmethod
    def shift_time_is_valid(cls, value: str) -> str:
        return validate_shift_time(value)

    @field_validator("joined_on")
    @classmethod
    def joined_on_is_valid(cls, value: str) -> str:
        return validate_date_string(value)

    @model_validator(mode="after")
    def shifts_differ(self) -> "EmployeeIn":
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start must differ from shift_end")
        return self


class PunchInIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    emp_code: str
    punched_at: Optional[EpochMillis] = None
    status: Literal["PRESENT", "WFH", "ON_DUTY"] = "PRESENT"


class PunchOutIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    emp_code: str
    punched_at: Optional[EpochMillis] = None


class RegularizeIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: Optional[Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]] = None
    punch_in: Optional[EpochMillis] = None
    punch_out: Optional[EpochMillis] = None
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def instants_must_not_be_null(self) -> "RegularizeIn":
        if "punch_in" in self.model_fields_set and self.punch_in is None:
            raise ValueError("punch_in cannot be null")
        if "punch_out" in self.model_fields_set and self.punch_out is None:
            raise ValueError("punch_out cannot be null")
        return self


# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    try:
        db.command("ping")
    except Exception as exc:
        raise HTTPException(503, "MongoDB is not ready") from exc
    return {"status": "ok"}


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    doc["created_at"] = utc_now_seconds()
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError as exc:
        raise HTTPException(409, "emp_code already exists") from exc
    return serialize_employee(doc)


@app.get("/employees")
def list_employees(department: Optional[str] = None, page: Page = 1, page_size: PageSize = 20):
    query = {"department": department} if department else {}
    skip = (page - 1) * page_size
    total = db.employees.count_documents(query)
    items = [
        serialize_employee(doc)
        for doc in db.employees.find(query).sort("emp_code", ASCENDING).skip(skip).limit(page_size)
    ]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, "employee not found")
    punched_at = normalize_instant(body.punched_at)
    attendance_date = attendance_date_for(punched_at, emp["shift_start"], emp["shift_end"])
    doc = {
        "emp_code": body.emp_code,
        "date": attendance_date,
        "status": body.status,
        "punch_in": punched_at,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(punched_at, emp["shift_start"], attendance_date),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError as exc:
        raise HTTPException(409, "already punched in for this date") from exc
    return serialize_attendance(doc)


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, "employee not found")
    punched_at = normalize_instant(body.punched_at)
    existing = db.attendance_logs.find_one(
        {"emp_code": body.emp_code, "punch_in": {"$lte": punched_at}},
        sort=[("punch_in", DESCENDING)],
    )
    if not existing:
        raise HTTPException(404, "no punch-in found")
    if existing.get("punch_out") is not None:
        raise HTTPException(409, "record already punched out")

    update = finalize_attendance_values({**existing, "punch_out": punched_at}, emp)
    updated = db.attendance_logs.find_one_and_update(
        {"_id": existing["_id"], "punch_out": None},
        {"$set": update},
        return_document=ReturnDocument.AFTER,
    )
    if not updated:
        raise HTTPException(409, "record already punched out")
    return serialize_attendance(updated)


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]] = None,
    page: Page = 1,
    page_size: PageSize = 20,
):
    query = build_attendance_query(emp_code, date_from, date_to, status)
    skip = (page - 1) * page_size
    total = db.attendance_logs.count_documents(query)
    docs = (
        db.attendance_logs.find(query)
        .sort([("date", DESCENDING), ("emp_code", ASCENDING)])
        .skip(skip)
        .limit(page_size)
    )
    return {
        "items": [serialize_attendance(doc) for doc in docs],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(emp_code: str, date: str, body: RegularizeIn):
    validate_date_string(date)
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(404, "employee not found")
    existing = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if not existing:
        raise HTTPException(404, "attendance record not found")

    requested: dict[str, Any] = {
        "reason": body.reason,
        "regularized_by": body.regularized_by,
    }
    if "status" in body.model_fields_set:
        requested["status"] = body.status
    if "punch_in" in body.model_fields_set:
        requested["punch_in"] = normalize_instant(body.punch_in)
    if "punch_out" in body.model_fields_set:
        requested["punch_out"] = normalize_instant(body.punch_out)

    update, history = build_regularization_update(existing, emp, requested)
    history_len = len(existing.get("history", []))
    updated = db.attendance_logs.find_one_and_update(
        {"_id": existing["_id"], f"history.{history_len}": {"$exists": False}},
        {"$set": update, "$push": {"history": history}},
        return_document=ReturnDocument.AFTER,
    )
    if not updated:
        raise HTTPException(409, "attendance record was changed concurrently")
    return serialize_attendance(updated)


# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #
