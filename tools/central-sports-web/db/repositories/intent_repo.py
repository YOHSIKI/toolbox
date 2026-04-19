"""予約予定（booking_intents）の CRUD。"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.domain.entities import BookingIntent, IntentStatus
from db.connection import read_connection, write_transaction
from db.repositories._common import format_datetime, parse_datetime


def _row_to_intent(row: sqlite3.Row) -> BookingIntent:
    seats_raw = row["seat_preferences"] or "[]"
    seats: list[int] = []
    try:
        parsed = json.loads(seats_raw)
        if isinstance(parsed, list):
            for v in parsed:
                try:
                    seats.append(int(v))
                except (TypeError, ValueError):
                    continue
    except json.JSONDecodeError:
        pass
    return BookingIntent(
        id=row["id"],
        lesson_date=date.fromisoformat(row["lesson_date"]),
        lesson_time=row["lesson_time"],
        program_id=row["program_id"],
        program_name=row["program_name"],
        studio_id=row["studio_id"],
        studio_room_id=row["studio_room_id"],
        seat_preferences=seats,
        status=IntentStatus(row["status"]),
        scheduled_run_at=parse_datetime(row["scheduled_run_at"]),
        executed_at=parse_datetime(row["executed_at"]),
        note=row["note"],
        created_at=parse_datetime(row["created_at"]),
        updated_at=parse_datetime(row["updated_at"]),
    )


def insert_intent(db_path: Path, item: BookingIntent) -> None:
    with write_transaction(db_path) as con:
        con.execute(
            """
            INSERT INTO booking_intents
              (id, lesson_date, lesson_time, program_id, program_name,
               studio_id, studio_room_id, seat_preferences, status,
               scheduled_run_at, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.id,
                item.lesson_date.isoformat(),
                item.lesson_time,
                item.program_id,
                item.program_name,
                item.studio_id,
                item.studio_room_id,
                json.dumps(item.seat_preferences, ensure_ascii=False),
                item.status.value,
                format_datetime(item.scheduled_run_at) if item.scheduled_run_at else "",
                item.note,
            ),
        )


def list_intents(
    db_path: Path,
    *,
    status: IntentStatus | None = None,
    pending_from: date | None = None,
) -> list[BookingIntent]:
    sql = "SELECT * FROM booking_intents WHERE 1=1"
    params: list[Any] = []
    if status is not None:
        sql += " AND status = ?"
        params.append(status.value)
    if pending_from is not None:
        sql += " AND lesson_date >= ?"
        params.append(pending_from.isoformat())
    sql += " ORDER BY scheduled_run_at, lesson_date, lesson_time"
    with read_connection(db_path) as con:
        rows = con.execute(sql, tuple(params)).fetchall()
    return [_row_to_intent(r) for r in rows]


def list_runnable_on(db_path: Path, target_day: date) -> list[BookingIntent]:
    """scheduled_run_at が指定日以前の pending な intent を返す（未実行を含む）。"""

    sql = (
        "SELECT * FROM booking_intents "
        "WHERE status = ? AND substr(scheduled_run_at, 1, 10) <= ? "
        "ORDER BY scheduled_run_at"
    )
    with read_connection(db_path) as con:
        rows = con.execute(
            sql, (IntentStatus.PENDING.value, target_day.isoformat())
        ).fetchall()
    return [_row_to_intent(r) for r in rows]


def get_intent(db_path: Path, intent_id: str) -> BookingIntent | None:
    with read_connection(db_path) as con:
        row = con.execute(
            "SELECT * FROM booking_intents WHERE id = ?", (intent_id,)
        ).fetchone()
    return _row_to_intent(row) if row else None


def update_status(
    db_path: Path,
    intent_id: str,
    status: IntentStatus,
    *,
    executed_at: datetime | None = None,
) -> None:
    with write_transaction(db_path) as con:
        con.execute(
            """
            UPDATE booking_intents
               SET status = ?,
                   executed_at = COALESCE(?, executed_at),
                   updated_at = datetime('now')
             WHERE id = ?
            """,
            (
                status.value,
                format_datetime(executed_at) if executed_at else None,
                intent_id,
            ),
        )


def delete_intent(db_path: Path, intent_id: str) -> None:
    with write_transaction(db_path) as con:
        con.execute("DELETE FROM booking_intents WHERE id = ?", (intent_id,))


def update_seat_preferences(
    db_path: Path, intent_id: str, seats: list[int]
) -> None:
    """希望席の優先順位だけを差し替える（status / lesson_date 等は触らない）。"""

    with write_transaction(db_path) as con:
        con.execute(
            """
            UPDATE booking_intents
               SET seat_preferences = ?,
                   updated_at = datetime('now')
             WHERE id = ?
            """,
            (json.dumps(seats, ensure_ascii=False), intent_id),
        )
