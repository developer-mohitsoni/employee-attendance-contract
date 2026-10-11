"""Attendance punch and regularization endpoints."""
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from ..database import db
from ..schemas import Page, PageSize, PunchInIn, PunchOutIn, RegularizeIn
from ..utils import (
    attendance_date_for,
    build_attendance_query,
    build_regularization_update,
    compute_late_minutes,
    finalize_attendance_values,
    normalize_instant,
    serialize_attendance,
    validate_date_string,
)

router = APIRouter()


@router.post("/attendance/punch-in", status_code=201)
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


@router.post("/attendance/punch-out")
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


@router.get("/attendance")
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


@router.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(emp_code: str, date: str, body: RegularizeIn):
    validate_date_string(date)
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(404, "employee not found")
    existing = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if not existing:
        raise HTTPException(404, "attendance record not found")

    requested = {"reason": body.reason, "regularized_by": body.regularized_by}
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
