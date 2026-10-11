"""FastAPI entrypoint for the Employee Attendance & Analytics API."""
from fastapi import FastAPI, HTTPException

from .database import db, ensure_indexes
from .routes import admin, analytics, attendance, employees

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")


@app.on_event("startup")
def on_startup() -> None:
    ensure_indexes()


@app.get("/health")
def health():
    try:
        db.command("ping")
    except Exception as exc:
        raise HTTPException(503, "MongoDB is not ready") from exc
    return {"status": "ok"}


app.include_router(employees.router)
app.include_router(attendance.router)
app.include_router(analytics.router)
app.include_router(admin.router)
