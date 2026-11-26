from __future__ import annotations

import multiprocessing
import sys
import tempfile
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.appointments import AppError, create_appointment, list_appointments
from backend.db import get_connection, init_db


def _worker(
    db_path: str,
    worker_id: int,
    resident_id: str,
    therapist_id: str,
    start_time: str,
    fire_at: float,
    queue: multiprocessing.Queue,
) -> None:
    conn = get_connection(db_path)
    try:
        # 所有进程提前建好数据库连接并在同一发令时刻发起写入，制造真实并发竞争
        wait_sec = fire_at - time.time()
        if wait_sec > 0:
            time.sleep(wait_sec)

        try:
            apt = create_appointment(
                conn,
                resident_id=resident_id,
                therapist_id=therapist_id,
                start_time=start_time,
                duration_minutes=45,
                notes=f"concurrency-worker-{worker_id}",
            )
            queue.put({"worker_id": worker_id, "ok": True, "code": "CREATED", "id": apt["id"]})
        except AppError as err:
            queue.put({"worker_id": worker_id, "ok": False, "code": err.code})
        except Exception as err:  # pragma: no cover
            queue.put({"worker_id": worker_id, "ok": False, "code": str(err)})
    finally:
        conn.close()


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="dorset-care-race-") as tmp_dir:
        db_path = str(Path(tmp_dir) / "race.db")
        init_db(db_path)

        worker_count = 6
        resident_ids = ["res-1", "res-2", "res-3", "res-4", "res-5"]
        therapist_id = "th-1"
        target_slot = "2026-12-15T05:00:00Z"
        fire_at = time.time() + 1.2

        print(
            f"Launching {worker_count} concurrent OS processes to book therapist {therapist_id} at {target_slot}..."
        )

        queue: multiprocessing.Queue = multiprocessing.Queue()
        procs = [
            multiprocessing.Process(
                target=_worker,
                args=(
                    db_path,
                    idx + 1,
                    resident_ids[idx % len(resident_ids)],
                    therapist_id,
                    target_slot,
                    fire_at,
                    queue,
                ),
            )
            for idx in range(worker_count)
        ]

        for p in procs:
            p.start()
        for p in procs:
            p.join()

        outcomes = sorted([queue.get() for _ in range(worker_count)], key=lambda x: x["worker_id"])
        for o in outcomes:
            status = "SUCCESS" if o["ok"] else "BLOCKED"
            print(f"  Worker #{o['worker_id']}: {status} ({o['code']})")

        winners = [o for o in outcomes if o["ok"]]
        conflicts = [
            o for o in outcomes if not o["ok"] and o["code"] in ("THERAPIST_CONFLICT", "BOTH_CONFLICT")
        ]

        assert len(winners) == 1, f"Expected exactly 1 winner, got {len(winners)}"
        assert len(conflicts) == worker_count - 1, (
            f"Expected {worker_count - 1} conflict rejections, got {len(conflicts)}"
        )

        verify_conn = get_connection(db_path)
        try:
            persisted = [
                a
                for a in list_appointments(verify_conn, status="SCHEDULED", therapist_id=therapist_id)
                if a["startTime"] == target_slot
            ]
        finally:
            verify_conn.close()

        assert len(persisted) == 1, f"Expected strictly 1 persisted row, found {len(persisted)}"
        print(
            f"PASS  Multi-process concurrency test: 1 succeeded, {len(conflicts)} cleanly rejected with conflict."
        )


if __name__ == "__main__":
    main()
