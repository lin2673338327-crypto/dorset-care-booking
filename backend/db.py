from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = BASE_DIR / "data" / "appointments.db"


def resolve_db_path(db_path: str | Path | None = None) -> str:
    if db_path is not None:
        return str(db_path)
    return os.environ.get("DB_PATH", str(DEFAULT_DB_PATH))


def get_connection(db_path: str | Path | None = None) -> sqlite3.Connection:
    """
    打开 SQLite 连接并配置并发安全参数：
    - isolation_level=None：关闭 Python sqlite3 隐式事务，由我们显式控制 BEGIN IMMEDIATE。
    - journal_mode=WAL：支持多读单写并发。
    - busy_timeout=5000：多个进程/请求同时写入时自动排队等待锁释放（最多 5 秒），而非立刻报错。
    """
    resolved = resolve_db_path(db_path)
    if resolved != ":memory:":
        Path(resolved).parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(resolved, timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


@contextmanager
def immediate_transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """
    显式 BEGIN IMMEDIATE 写锁事务：
    1. 一进入事务立刻申请 RESERVED 写锁，强制并发写入严格串行排队（防重复预约）。
    2. 避免普通 BEGIN 先读后升级写锁导致的 SQLite 锁升级死锁。
    3. 发生任意异常时立即 ROLLBACK，确保改期失败时原预约完整保留。
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise


def init_db(db_path: str | Path | None = None) -> None:
    conn = get_connection(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS therapists (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                specialty TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS residents (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                room TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS appointments (
                id TEXT PRIMARY KEY,
                resident_id TEXT NOT NULL REFERENCES residents(id),
                therapist_id TEXT NOT NULL REFERENCES therapists(id),
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                start_ms INTEGER NOT NULL,
                end_ms INTEGER NOT NULL,
                duration_minutes INTEGER NOT NULL CHECK (duration_minutes IN (30, 45, 60)),
                status TEXT NOT NULL DEFAULT 'SCHEDULED' CHECK (status IN ('SCHEDULED', 'CANCELLED')),
                notes TEXT NOT NULL DEFAULT '',
                version INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_appointments_therapist_time
                ON appointments (therapist_id, status, start_ms, end_ms);

            CREATE INDEX IF NOT EXISTS idx_appointments_resident_time
                ON appointments (resident_id, status, start_ms, end_ms);
            """
        )
        _seed_fictional_data(conn)
    finally:
        conn.close()


def _seed_fictional_data(conn: sqlite3.Connection) -> None:
    row = conn.execute("SELECT COUNT(*) AS cnt FROM therapists").fetchone()
    if row and row["cnt"] > 0:
        return

    with immediate_transaction(conn):
        recheck = conn.execute("SELECT COUNT(*) AS cnt FROM therapists").fetchone()
        if recheck and recheck["cnt"] > 0:
            return

        therapists = [
            ("th-1", "Sarah Chen", "Physiotherapy (物理治疗)"),
            ("th-2", "Liam O'Connor", "Occupational Therapy (职业治疗)"),
            ("th-3", "Priya Patel", "Speech Therapy (语言治疗)"),
            ("th-4", "David Brooks", "Mobility & Massage (行动复健)"),
        ]
        conn.executemany(
            "INSERT INTO therapists (id, name, specialty) VALUES (?, ?, ?)",
            therapists,
        )

        residents = [
            ("res-1", "Arthur Miller", "Room 101A"),
            ("res-2", "Margaret Wilson", "Room 104B"),
            ("res-3", "Robert Zhang", "Room 202A"),
            ("res-4", "Evelyn Davies", "Room 205C"),
            ("res-5", "George Thompson", "Room 301A"),
        ]
        conn.executemany(
            "INSERT INTO residents (id, name, room) VALUES (?, ?, ?)",
            residents,
        )

        # 基于本地时区的明天生成 3 条虚构示例预约，打开即有数据可体验
        local_tz = datetime.now().astimezone().tzinfo
        tomorrow = datetime.now(local_tz).date() + timedelta(days=1)
        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        samples = [
            ("apt-seed-1", "res-1", "th-1", 9, 0, 45, "膝盖术后复健训练"),
            ("apt-seed-2", "res-2", "th-2", 10, 0, 30, "日常抓握与平衡练习"),
            ("apt-seed-3", "res-3", "th-1", 10, 30, 60, "肩颈伸展与步行辅助"),
        ]
        for apt_id, resident_id, therapist_id, hour, minute, duration, notes in samples:
            start_local = datetime(
                tomorrow.year, tomorrow.month, tomorrow.day, hour, minute, tzinfo=local_tz
            )
            start_utc = start_local.astimezone(timezone.utc)
            end_utc = start_utc + timedelta(minutes=duration)
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
                    start_utc.isoformat().replace("+00:00", "Z"),
                    end_utc.isoformat().replace("+00:00", "Z"),
                    int(start_utc.timestamp() * 1000),
                    int(end_utc.timestamp() * 1000),
                    duration,
                    notes,
                    now_iso,
                    now_iso,
                ),
            )
