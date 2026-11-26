from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .appointments import (
    ALLOWED_DURATIONS,
    AppError,
    cancel_appointment,
    create_appointment,
    list_appointments,
    list_residents,
    list_therapists,
    reschedule_appointment,
)
from .db import BASE_DIR, get_connection, init_db

STATIC_DIR = BASE_DIR / "static"


class CreateAppointmentPayload(BaseModel):
    residentId: str
    therapistId: str
    startTime: str
    durationMinutes: int
    notes: str = ""


class RescheduleAppointmentPayload(BaseModel):
    therapistId: str | None = None
    startTime: str
    durationMinutes: int
    notes: str | None = None
    expectedVersion: int | None = None


class CancelAppointmentPayload(BaseModel):
    expectedVersion: int | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Dorset Care · 养老院治疗预约系统 API",
    version="1.0.0",
    lifespan=lifespan,
)


@app.exception_handler(AppError)
async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            }
        },
    )


@app.get("/api/meta")
def api_meta():
    conn = get_connection()
    try:
        return {
            "therapists": list_therapists(conn),
            "residents": list_residents(conn),
            "allowedDurations": list(ALLOWED_DURATIONS),
        }
    finally:
        conn.close()


@app.get("/api/appointments")
def api_list_appointments(
    status: str = Query(default="ALL"),
    therapistId: str | None = Query(default=None),
    residentId: str | None = Query(default=None),
):
    conn = get_connection()
    try:
        items = list_appointments(
            conn,
            status=status,
            therapist_id=therapistId or None,
            resident_id=residentId or None,
        )
        return {"appointments": items}
    finally:
        conn.close()


@app.post("/api/appointments", status_code=201)
def api_create_appointment(payload: CreateAppointmentPayload):
    conn = get_connection()
    try:
        apt = create_appointment(
            conn,
            resident_id=payload.residentId,
            therapist_id=payload.therapistId,
            start_time=payload.startTime,
            duration_minutes=payload.durationMinutes,
            notes=payload.notes,
        )
        return {"appointment": apt}
    finally:
        conn.close()


@app.patch("/api/appointments/{appointment_id}/reschedule")
def api_reschedule_appointment(
    appointment_id: str, payload: RescheduleAppointmentPayload
):
    conn = get_connection()
    try:
        apt = reschedule_appointment(
            conn,
            appointment_id,
            therapist_id=payload.therapistId,
            start_time=payload.startTime,
            duration_minutes=payload.durationMinutes,
            notes=payload.notes,
            expected_version=payload.expectedVersion,
        )
        return {"appointment": apt}
    finally:
        conn.close()


@app.post("/api/appointments/{appointment_id}/cancel")
def api_cancel_appointment(
    appointment_id: str, payload: CancelAppointmentPayload | None = None
):
    conn = get_connection()
    try:
        expected_version = payload.expectedVersion if payload else None
        apt = cancel_appointment(conn, appointment_id, expected_version)
        return {"appointment": apt}
    finally:
        conn.close()


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def serve_index() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"))
