"""Employee endpoints."""
from typing import Optional

from fastapi import APIRouter, HTTPException
from pymongo import ASCENDING
from pymongo.errors import DuplicateKeyError

from ..database import db
from ..schemas import EmployeeIn, Page, PageSize
from ..utils import serialize_employee, utc_now_seconds

router = APIRouter()


@router.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    doc["created_at"] = utc_now_seconds()
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError as exc:
        raise HTTPException(409, "emp_code already exists") from exc
    return serialize_employee(doc)


@router.get("/employees")
def list_employees(department: Optional[str] = None, page: Page = 1, page_size: PageSize = 20):
    query = {"department": department} if department else {}
    skip = (page - 1) * page_size
    total = db.employees.count_documents(query)
    items = [
        serialize_employee(doc)
        for doc in db.employees.find(query).sort("emp_code", ASCENDING).skip(skip).limit(page_size)
    ]
    return {"items": items, "total": total, "page": page, "page_size": page_size}
