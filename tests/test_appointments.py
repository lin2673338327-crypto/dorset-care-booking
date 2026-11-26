from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.appointments import (
    AppError,
    cancel_appointment,
    create_appointment,
    get_appointment_by_id,
    list_appointments,
    reschedule_appointment,
)
from backend.db import get_connection, init_db
from backend.main import (
    CreateAppointmentPayload,
    api_create_appointment,
    app_error_handler,
    serve_index,
)


def iso_at(day_offset: int, hour: int, minute: int) -> str:
    dt = datetime(2026, 12, 1, hour, minute, tzinfo=timezone.utc) + timedelta(days=day_offset)
    return dt.isoformat().replace("+00:00", "Z")


class TestAppointmentsDomain(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory(prefix="dorset-care-py-")
        self.db_path = Path(self.tmp_dir.name) / "test.db"
        init_db(self.db_path)
        self.conn = get_connection(self.db_path)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp_dir.cleanup()

    def test_1_create_valid_and_reject_invalid_duration(self) -> None:
        created = create_appointment(
            self.conn,
            resident_id="res-1",
            therapist_id="th-1",
            start_time=iso_at(1, 9, 0),
            duration_minutes=45,
            notes="初始复健",
        )
        self.assertEqual(created["durationMinutes"], 45)
        self.assertEqual(created["status"], "SCHEDULED")
        self.assertEqual(created["version"], 1)

        with self.assertRaises(AppError) as ctx:
            create_appointment(
                self.conn,
                resident_id="res-2",
                therapist_id="th-2",
                start_time=iso_at(1, 9, 0),
                duration_minutes=20,
            )
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.code, "VALIDATION_ERROR")

    def test_2_block_therapist_overlap(self) -> None:
        create_appointment(
            self.conn,
            resident_id="res-1",
            therapist_id="th-1",
            start_time=iso_at(1, 9, 0),
            duration_minutes=45,
        )
        with self.assertRaises(AppError) as ctx:
            create_appointment(
                self.conn,
                resident_id="res-2",
                therapist_id="th-1",
                start_time=iso_at(1, 9, 30),
                duration_minutes=30,
            )
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.code, "THERAPIST_CONFLICT")

    def test_3_block_resident_overlap(self) -> None:
        create_appointment(
            self.conn,
            resident_id="res-1",
            therapist_id="th-1",
            start_time=iso_at(1, 9, 0),
            duration_minutes=45,
        )
        with self.assertRaises(AppError) as ctx:
            create_appointment(
                self.conn,
                resident_id="res-1",
                therapist_id="th-2",
                start_time=iso_at(1, 9, 15),
                duration_minutes=30,
            )
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.code, "RESIDENT_CONFLICT")

    def test_4_allow_back_to_back_adjacent_slot(self) -> None:
        create_appointment(
            self.conn,
            resident_id="res-1",
            therapist_id="th-1",
            start_time=iso_at(1, 9, 0),
            duration_minutes=45,
        )
        adjacent = create_appointment(
            self.conn,
            resident_id="res-1",
            therapist_id="th-1",
            start_time=iso_at(1, 9, 45),
            duration_minutes=30,
            notes="紧接上一场",
        )
        self.assertEqual(adjacent["startTime"], iso_at(1, 9, 45))
        self.assertEqual(adjacent["durationMinutes"], 30)

    def test_5_reschedule_failure_preserves_original_appointment(self) -> None:
        target = create_appointment(
            self.conn,
            resident_id="res-3",
            therapist_id="th-3",
            start_time=iso_at(2, 10, 0),
            duration_minutes=30,
            notes="原预约记录",
        )
        create_appointment(
            self.conn,
            resident_id="res-4",
            therapist_id="th-3",
            start_time=iso_at(2, 11, 0),
            duration_minutes=60,
        )

        with self.assertRaises(AppError) as ctx:
            reschedule_appointment(
                self.conn,
                target["id"],
                start_time=iso_at(2, 11, 30),
                duration_minutes=30,
                expected_version=target["version"],
            )
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.code, "THERAPIST_CONFLICT")

        after = get_appointment_by_id(self.conn, target["id"])
        assert after is not None
        self.assertEqual(after["startTime"], iso_at(2, 10, 0))
        self.assertEqual(after["durationMinutes"], 30)
        self.assertEqual(after["version"], 1)
        self.assertEqual(after["notes"], "原预约记录")

    def test_6_optimistic_lock_blocks_stale_version(self) -> None:
        apt = create_appointment(
            self.conn,
            resident_id="res-5",
            therapist_id="th-4",
            start_time=iso_at(3, 14, 0),
            duration_minutes=30,
        )
        updated = reschedule_appointment(
            self.conn,
            apt["id"],
            start_time=iso_at(3, 15, 0),
            duration_minutes=60,
            expected_version=1,
        )
        self.assertEqual(updated["version"], 2)

        with self.assertRaises(AppError) as ctx:
            reschedule_appointment(
                self.conn,
                apt["id"],
                start_time=iso_at(3, 16, 0),
                duration_minutes=30,
                expected_version=1,
            )
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.code, "VERSION_CONFLICT")

    def test_7_cancel_soft_deletes_and_releases_slot(self) -> None:
        apt = create_appointment(
            self.conn,
            resident_id="res-2",
            therapist_id="th-2",
            start_time=iso_at(4, 10, 0),
            duration_minutes=45,
        )
        cancelled = cancel_appointment(self.conn, apt["id"], apt["version"])
        self.assertEqual(cancelled["status"], "CANCELLED")

        rebooked = create_appointment(
            self.conn,
            resident_id="res-3",
            therapist_id="th-2",
            start_time=iso_at(4, 10, 0),
            duration_minutes=45,
        )
        self.assertEqual(rebooked["status"], "SCHEDULED")

        cancelled_rows = list_appointments(self.conn, status="CANCELLED")
        self.assertTrue(any(a["id"] == apt["id"] for a in cancelled_rows))

    def test_8_http_api_conflict_error_contract(self) -> None:
        prev_db = os.environ.get("DB_PATH")
        os.environ["DB_PATH"] = str(self.db_path)
        try:
            index_resp = serve_index()
            self.assertTrue(Path(index_resp.path).exists())

            created = api_create_appointment(
                CreateAppointmentPayload(
                    residentId="res-1",
                    therapistId="th-1",
                    startTime=iso_at(5, 10, 0),
                    durationMinutes=30,
                )
            )
            self.assertEqual(created["appointment"]["status"], "SCHEDULED")

            with self.assertRaises(AppError) as ctx:
                api_create_appointment(
                    CreateAppointmentPayload(
                        residentId="res-2",
                        therapistId="th-1",
                        startTime=iso_at(5, 10, 15),
                        durationMinutes=30,
                    )
                )

            coro = app_error_handler(None, ctx.exception)  # type: ignore[arg-type]
            try:
                coro.send(None)
            except StopIteration as stop:
                resp = stop.value
            self.assertEqual(resp.status_code, 409)
            body = json.loads(resp.body.decode("utf-8"))
            self.assertEqual(body["error"]["code"], "THERAPIST_CONFLICT")
            self.assertIn("Sarah Chen", body["error"]["message"])
        finally:
            if prev_db is None:
                os.environ.pop("DB_PATH", None)
            else:
                os.environ["DB_PATH"] = prev_db


if __name__ == "__main__":
    unittest.main(verbosity=2)
