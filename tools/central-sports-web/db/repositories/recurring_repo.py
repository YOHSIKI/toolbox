"""定期予約の読み書き。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from app.domain.entities import RecurringReservation, RecurringStatus
from db.connection import read_connection, write_transaction
from db.repositories._common import parse_datetime


def _row_to_recurring(row: sqlite3.Row) -> RecurringReservation:
    seats_raw = row["seat_preferences"] or "[]"
    seats_list: list[int] = []
    try:
        parsed = json.loads(seats_raw)
        if isinstance(parsed, list):
            for item in parsed:
                try:
                    seats_list.append(int(item))
                except (TypeError, ValueError):
                    continue
    except json.JSONDecodeError:
        pass
    return RecurringReservation(
        id=row["id"],
        day_of_week=row["day_of_week"],
        start_time=row["start_time"],
        program_id=row["program_id"],
        program_name=row["program_name"],
        studio_id=row["studio_id"],
        studio_room_id=row["studio_room_id"],
        seat_preferences=seats_list,
        status=RecurringStatus(row["status"]),
        note=row["note"],
        created_at=parse_datetime(row["created_at"]),
        updated_at=parse_datetime(row["updated_at"]),
    )


def list_recurring(
    db_path: Path, *, include_deleted: bool = False
) -> list[RecurringReservation]:
    sql = "SELECT * FROM recurring_reservations"
    params: tuple = ()
    if not include_deleted:
        sql += " WHERE status != ?"
        params = (RecurringStatus.DELETED.value,)
    sql += " ORDER BY day_of_week, start_time"
    with read_connection(db_path) as con:
        rows = con.execute(sql, params).fetchall()
    return [_row_to_recurring(r) for r in rows]


def get_recurring(db_path: Path, recurring_id: str) -> RecurringReservation | None:
    with read_connection(db_path) as con:
        row = con.execute(
            "SELECT * FROM recurring_reservations WHERE id = ?",
            (recurring_id,),
        ).fetchone()
    return _row_to_recurring(row) if row else None


def insert_recurring(db_path: Path, item: RecurringReservation) -> None:
    with write_transaction(db_path) as con:
        con.execute(
            """
            INSERT INTO recurring_reservations
              (id, day_of_week, start_time, program_id, program_name,
               studio_id, studio_room_id, seat_preferences, status, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.id,
                item.day_of_week,
                item.start_time,
                item.program_id,
                item.program_name,
                item.studio_id,
                item.studio_room_id,
                json.dumps(item.seat_preferences, ensure_ascii=False),
                item.status.value,
                item.note,
            ),
        )


def update_recurring(db_path: Path, item: RecurringReservation) -> None:
    with write_transaction(db_path) as con:
        con.execute(
            """
            UPDATE recurring_reservations
               SET day_of_week = ?,
                   start_time = ?,
                   program_id = ?,
                   program_name = ?,
                   studio_id = ?,
                   studio_room_id = ?,
                   seat_preferences = ?,
                   status = ?,
                   note = ?,
                   updated_at = datetime('now')
             WHERE id = ?
            """,
            (
                item.day_of_week,
                item.start_time,
                item.program_id,
                item.program_name,
                item.studio_id,
                item.studio_room_id,
                json.dumps(item.seat_preferences, ensure_ascii=False),
                item.status.value,
                item.note,
                item.id,
            ),
        )


def update_status(
    db_path: Path, recurring_id: str, status: RecurringStatus
) -> None:
    with write_transaction(db_path) as con:
        con.execute(
            "UPDATE recurring_reservations SET status = ?, updated_at = datetime('now') WHERE id = ?",
            (status.value, recurring_id),
        )
