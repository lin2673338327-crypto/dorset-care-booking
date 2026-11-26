from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from .db import immediate_transaction

ALLOWED_DURATIONS = (30, 45, 60)

SELECT_APPOINTMENT_SQL = """
    SELECT
        a.id,
        a.resident_id,
        r.name AS resident_name,
        r.room AS resident_room,
        a.therapist_id,
        t.name AS therapist_name,
        t.specialty AS therapist_specialty,
        a.start_time,
        a.end_time,
        a.start_ms,
        a.end_ms,
        a.duration_minutes,
        a.status,
        a.notes,
        a.version,
        a.created_at,
        a.updated_at
    FROM appointments a
    JOIN residents r ON r.id = a.resident_id
    JOIN therapists t ON t.id = a.therapist_id
"""


class AppError(Exception):
    def __init__(
        self,
        message: str,
        status_code: int,
        code: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
        self.details = details


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "residentId": row["resident_id"],
        "residentName": row["resident_name"],
        "residentRoom": row["resident_room"],
        "therapistId": row["therapist_id"],
        "therapistName": row["therapist_name"],
        "therapistSpecialty": row["therapist_specialty"],
        "startTime": row["start_time"],
        "endTime": row["end_time"],
        "durationMinutes": row["duration_minutes"],
        "status": row["status"],
        "notes": row["notes"],
        "version": row["version"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_time_range(start_time: str, duration_minutes: int) -> dict[str, Any]:
    if duration_minutes not in ALLOWED_DURATIONS:
        raise AppError(
            "治疗时长仅支持 30、45 或 60 分钟。",
            400,
            "VALIDATION_ERROR",
            {"allowedDurations": list(ALLOWED_DURATIONS)},
        )

    if not start_time or not isinstance(start_time, str):
        raise AppError("开始时间格式无效。", 400, "VALIDATION_ERROR")

    try:
        parsed = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AppError("开始时间格式无效。", 400, "VALIDATION_ERROR") from exc

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)

    start_utc = parsed.astimezone(timezone.utc)
    end_utc = start_utc + timedelta(minutes=duration_minutes)

    return {
        "start_iso": start_utc.isoformat().replace("+00:00", "Z"),
        "end_iso": end_utc.isoformat().replace("+00:00", "Z"),
        "start_ms": int(start_utc.timestamp() * 1000),
        "end_ms": int(end_utc.timestamp() * 1000),
        "duration_minutes": duration_minutes,
    }


def _ensure_entities_exist(
    conn: sqlite3.Connection, resident_id: str, therapist_id: str
) -> None:
    if not conn.execute("SELECT id FROM residents WHERE id = ?", (resident_id,)).fetchone():
        raise AppError("所选住民不存在。", 400, "VALIDATION_ERROR")
    if not conn.execute("SELECT id FROM therapists WHERE id = ?", (therapist_id,)).fetchone():
        raise AppError("所选治疗师不存在。", 400, "VALIDATION_ERROR")


def _assert_no_time_conflict(
    conn: sqlite3.Connection,
    *,
    resident_id: str,
    therapist_id: str,
    start_ms: int,
    end_ms: int,
    exclude_id: str | None = None,
) -> None:
    """
    核心冲突检查（左闭右开区间 [start, end)）：
    - 仅检查 status = 'SCHEDULED' 的有效预约（软删除取消后自动释放时段）。
    - 重叠条件：existing.start_ms < new_end_ms AND existing.end_ms > new_start_ms
      （例如 09:00-09:45 与 09:45-10:15 首尾相接不重叠，允许预约）。
    """
    conflicts = conn.execute(
        f"""
        {SELECT_APPOINTMENT_SQL}
        WHERE a.status = 'SCHEDULED'
          AND (a.therapist_id = ? OR a.resident_id = ?)
          AND a.start_ms < ?
          AND a.end_ms > ?
          AND (? IS NULL OR a.id != ?)
        ORDER BY a.start_ms ASC
        """,
        (therapist_id, resident_id, end_ms, start_ms, exclude_id, exclude_id),
    ).fetchall()

    if not conflicts:
        return

    therapist_conflict = next((c for c in conflicts if c["therapist_id"] == therapist_id), None)
    resident_conflict = next((c for c in conflicts if c["resident_id"] == resident_id), None)

    if therapist_conflict and resident_conflict:
        raise AppError(
            f"时间冲突：治疗师 {therapist_conflict['therapist_name']} 与住民 {resident_conflict['resident_name']} 在此时段均已有预约。",
            409,
            "BOTH_CONFLICT",
            {"conflictingAppointmentIds": [c["id"] for c in conflicts]},
        )

    if therapist_conflict:
        raise AppError(
            f"治疗师时间冲突：{therapist_conflict['therapist_name']} 在此时段已有其他预约。",
            409,
            "THERAPIST_CONFLICT",
            {"conflictingAppointmentId": therapist_conflict["id"]},
        )

    if resident_conflict:
        raise AppError(
            f"住民时间冲突：{resident_conflict['resident_name']} 在此时段已有其他预约。",
            409,
            "RESIDENT_CONFLICT",
            {"conflictingAppointmentId": resident_conflict["id"]},
        )


def list_therapists(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT id, name, specialty FROM therapists ORDER BY name ASC").fetchall()
    return [dict(r) for r in rows]


def list_residents(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT id, name, room FROM residents ORDER BY room ASC").fetchall()
    return [dict(r) for r in rows]


def list_appointments(
    conn: sqlite3.Connection,
    *,
    status: str = "ALL",
    therapist_id: str | None = None,
    resident_id: str | None = None,
) -> list[dict[str, Any]]:
    conditions: list[str] = []
    values: list[Any] = []

    if status in ("SCHEDULED", "CANCELLED"):
        conditions.append("a.status = ?")
        values.append(status)
    if therapist_id:
        conditions.append("a.therapist_id = ?")
        values.append(therapist_id)
    if resident_id:
        conditions.append("a.resident_id = ?")
        values.append(resident_id)

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = conn.execute(
        f"{SELECT_APPOINTMENT_SQL} {where_clause} ORDER BY a.start_ms ASC",
        values,
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_appointment_by_id(conn: sqlite3.Connection, appointment_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        f"{SELECT_APPOINTMENT_SQL} WHERE a.id = ?",
        (appointment_id,),
    ).fetchone()
    return _row_to_dict(row) if row else None


def create_appointment(
    conn: sqlite3.Connection,
    *,
    resident_id: str,
    therapist_id: str,
    start_time: str,
    duration_minutes: int,
    notes: str = "",
) -> dict[str, Any]:
    """
    新增预约：在 BEGIN IMMEDIATE 写锁事务内完成校验与写入，确保并发抢订只有一笔成功。
    """
    time_range = _validate_time_range(start_time, duration_minutes)
    clean_notes = (notes or "").strip()

    with immediate_transaction(conn):
        _ensure_entities_exist(conn, resident_id, therapist_id)
        _assert_no_time_conflict(
            conn,
            resident_id=resident_id,
            therapist_id=therapist_id,
            start_ms=time_range["start_ms"],
            end_ms=time_range["end_ms"],
        )

        apt_id = f"apt-{uuid.uuid4().hex[:8]}"
        now = _now_iso()
        conn.execute(
            """
            INSERT INTO appointments (
                id, resident_id, therapist_id,
                start_time, end_time, start_ms, end_ms,
                duration_minutes, status, notes, version, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'SCHEDULED', ?, 1, ?, ?)
            """,
            (
                apt_id,
                resident_id,
                therapist_id,
                time_range["start_iso"],
                time_range["end_iso"],
                time_range["start_ms"],
                time_range["end_ms"],
                time_range["duration_minutes"],
                clean_notes,
                now,
                now,
            ),
        )
        created = get_appointment_by_id(conn, apt_id)
        assert created is not None
        return created


def reschedule_appointment(
    conn: sqlite3.Connection,
    appointment_id: str,
    *,
    start_time: str,
    duration_minutes: int,
    therapist_id: str | None = None,
    notes: str | None = None,
    expected_version: int | None = None,
) -> dict[str, Any]:
    """
    改期预约：在同一个 BEGIN IMMEDIATE 事务中校验存在性、乐观锁版本号与新时段冲突。
    任一步失败立刻 ROLLBACK，确保原预约完整保留。
    """
    time_range = _validate_time_range(start_time, duration_minutes)

    with immediate_transaction(conn):
        existing = conn.execute(
            "SELECT * FROM appointments WHERE id = ?", (appointment_id,)
        ).fetchone()
        if not existing:
            raise AppError("找不到该预约记录。", 404, "NOT_FOUND")
        if existing["status"] != "SCHEDULED":
            raise AppError("已取消的预约无法改期，请重新建立新预约。", 409, "ALREADY_CANCELLED")
        if expected_version is not None and existing["version"] != expected_version:
            raise AppError(
                "该预约刚被其他员工更新，请刷新列表后再重试改期。",
                409,
                "VERSION_CONFLICT",
                {
                    "expectedVersion": expected_version,
                    "currentVersion": existing["version"],
                },
            )

        next_therapist_id = therapist_id or existing["therapist_id"]
        _ensure_entities_exist(conn, existing["resident_id"], next_therapist_id)
        _assert_no_time_conflict(
            conn,
            resident_id=existing["resident_id"],
            therapist_id=next_therapist_id,
            start_ms=time_range["start_ms"],
            end_ms=time_range["end_ms"],
            exclude_id=appointment_id,
        )

        next_notes = notes.strip() if notes is not None else existing["notes"]
        conn.execute(
            """
            UPDATE appointments
            SET therapist_id = ?,
                start_time = ?,
                end_time = ?,
                start_ms = ?,
                end_ms = ?,
                duration_minutes = ?,
                notes = ?,
                version = version + 1,
                updated_at = ?
            WHERE id = ?
            """,
            (
                next_therapist_id,
                time_range["start_iso"],
                time_range["end_iso"],
                time_range["start_ms"],
                time_range["end_ms"],
                time_range["duration_minutes"],
                next_notes,
                _now_iso(),
                appointment_id,
            ),
        )
        updated = get_appointment_by_id(conn, appointment_id)
        assert updated is not None
        return updated


def cancel_appointment(
    conn: sqlite3.Connection,
    appointment_id: str,
    expected_version: int | None = None,
) -> dict[str, Any]:
    """
    取消预约（软删除）：将 status 更新为 CANCELLED，保留历史稽核记录并释放时段。
    """
    with immediate_transaction(conn):
        existing = conn.execute(
            "SELECT id, status, version FROM appointments WHERE id = ?",
            (appointment_id,),
        ).fetchone()
        if not existing:
            raise AppError("找不到该预约记录。", 404, "NOT_FOUND")
        if existing["status"] == "CANCELLED":
            raise AppError("该预约已经是取消状态。", 409, "ALREADY_CANCELLED")
        if expected_version is not None and existing["version"] != expected_version:
            raise AppError(
                "该预约刚被其他员工更新，请刷新列表后再重试取消。",
                409,
                "VERSION_CONFLICT",
                {
                    "expectedVersion": expected_version,
                    "currentVersion": existing["version"],
                },
            )

        conn.execute(
            """
            UPDATE appointments
            SET status = 'CANCELLED',
                version = version + 1,
                updated_at = ?
            WHERE id = ?
            """,
            (_now_iso(), appointment_id),
        )
        cancelled = get_appointment_by_id(conn, appointment_id)
        assert cancelled is not None
        return cancelled
