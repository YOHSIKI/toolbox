"""予約の読み書き。

hacomono の予約 1 件に対応。由来（single / recurring）と元 ID を保持する。
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

from app.domain.entities import (
    Reservation,
    ReservationOrigin,
    ReservationStatus,
)
from db.connection import read_connection, write_transaction
from db.repositories._common import parse_datetime


def _row_to_reservation(row: sqlite3.Row) -> Reservation:
    return Reservation(
        id=row["id"],
        external_id=row["external_id"],
        studio_lesson_id=row["studio_lesson_id"] or 0,
        lesson_date=date.fromisoformat(row["lesson_date"]),
        lesson_time=row["lesson_time"],
        program_id=row["program_id"],
        program_name=row["program_name"],
        instructor_name=row["instructor_name"],
        studio_id=row["studio_id"],
        studio_room_id=row["studio_room_id"],
        seat_no=row["seat_no"],
        origin=ReservationOrigin(row["origin"]),
        origin_id=row["origin_id"],
        status=ReservationStatus(row["status"]),
        created_at=parse_datetime(row["created_at"]),
        updated_at=parse_datetime(row["updated_at"]),
    )


def list_reservations(
    db_path: Path,
    *,
    status: ReservationStatus | None = ReservationStatus.CONFIRMED,
    since: date | None = None,
) -> list[Reservation]:
    sql = "SELECT * FROM reservations WHERE 1=1"
    params: list[Any] = []
    if status is not None:
        sql += " AND status = ?"
        params.append(status.value)
    if since is not None:
        sql += " AND lesson_date >= ?"
        params.append(since.isoformat())
    sql += " ORDER BY lesson_date, lesson_time"
    with read_connection(db_path) as con:
        rows = con.execute(sql, tuple(params)).fetchall()
    return [_row_to_reservation(r) for r in rows]


def get_by_id(db_path: Path, reservation_id: str) -> Reservation | None:
    with read_connection(db_path) as con:
        row = con.execute(
            "SELECT * FROM reservations WHERE id = ?", (reservation_id,)
        ).fetchone()
    return _row_to_reservation(row) if row else None


def get_by_external_id(
    db_path: Path, external_id: int
) -> Reservation | None:
    with read_connection(db_path) as con:
        row = con.execute(
            "SELECT * FROM reservations WHERE external_id = ?",
            (external_id,),
        ).fetchone()
    return _row_to_reservation(row) if row else None


def upsert_reservation(db_path: Path, item: Reservation) -> None:
    """external_id を軸に UPSERT する。list_my_reservations との同期で使う。"""

    with write_transaction(db_path) as con:
        con.execute(
            """
            INSERT INTO reservations
              (id, external_id, studio_lesson_id, lesson_date, lesson_time,
               program_id, program_name, instructor_name,
               studio_id, studio_room_id, seat_no, origin, origin_id, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(external_id) DO UPDATE SET
              studio_lesson_id = excluded.studio_lesson_id,
              lesson_date      = excluded.lesson_date,
              lesson_time      = excluded.lesson_time,
              program_id       = excluded.program_id,
              program_name     = excluded.program_name,
              instructor_name  = excluded.instructor_name,
              studio_id        = excluded.studio_id,
              studio_room_id   = excluded.studio_room_id,
              seat_no          = excluded.seat_no,
              status           = excluded.status,
              updated_at       = datetime('now')
            """,
            (
                item.id,
                item.external_id,
                item.studio_lesson_id,
                item.lesson_date.isoformat(),
                item.lesson_time,
                item.program_id,
                item.program_name,
                item.instructor_name,
                item.studio_id,
                item.studio_room_id,
                item.seat_no,
                item.origin.value,
                item.origin_id,
                item.status.value,
            ),
        )


def update_status(
    db_path: Path, reservation_id: str, status: ReservationStatus
) -> None:
    with write_transaction(db_path) as con:
        con.execute(
            "UPDATE reservations SET status = ?, updated_at = datetime('now') WHERE id = ?",
            (status.value, reservation_id),
        )


def update_seat(db_path: Path, reservation_id: str, seat_no: int) -> None:
    with write_transaction(db_path) as con:
        con.execute(
            "UPDATE reservations SET seat_no = ?, updated_at = datetime('now') WHERE id = ?",
            (seat_no, reservation_id),
        )


def mark_missing_as_cancelled(
    db_path: Path, active_external_ids: list[int]
) -> list[int]:
    """hacomono 側に存在しない予約をローカルでキャンセル扱いに更新。

    今回の呼び出しで実際に cancelled に更新した external_id のリストを返す。
    呼び出し側はこのリストを使って history に reservation.cancel を記録でき、
    「build 前に cancelled 化済み → 次回の sync で検出できず history 欠落」
    という取りこぼしを防げる。
    """

    with write_transaction(db_path) as con:
        if active_external_ids:
            placeholders = ",".join("?" * len(active_external_ids))
            cur = con.execute(
                f"""
                UPDATE reservations
                   SET status = ?,
                       updated_at = datetime('now')
                 WHERE status = ?
                   AND external_id IS NOT NULL
                   AND external_id NOT IN ({placeholders})
                RETURNING external_id
                """,
                (ReservationStatus.CANCELLED.value, ReservationStatus.CONFIRMED.value, *active_external_ids),
            )
        else:
            cur = con.execute(
                """
                UPDATE reservations
                   SET status = ?,
                       updated_at = datetime('now')
                 WHERE status = ?
                   AND external_id IS NOT NULL
                RETURNING external_id
                """,
                (ReservationStatus.CANCELLED.value, ReservationStatus.CONFIRMED.value),
            )
        return [row["external_id"] for row in cur.fetchall()]
