"""店舗マスターの読み書き。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.domain.entities import Studio
from db.connection import read_connection, write_transaction


def _row_to_studio(row: sqlite3.Row) -> Studio:
    return Studio(
        id=row["id"],
        studio_id=row["studio_id"],
        studio_room_id=row["studio_room_id"],
        display_name=row["display_name"],
        club_code=row["club_code"],
        sisetcd=row["sisetcd"],
        is_default=bool(row["is_default"]),
    )


def list_studios(db_path: Path) -> list[Studio]:
    with read_connection(db_path) as con:
        rows = con.execute(
            "SELECT * FROM studios ORDER BY is_default DESC, sort_order, display_name"
        ).fetchall()
    return [_row_to_studio(r) for r in rows]


def get_studio_by_id(db_path: Path, studio_pk: int) -> Studio | None:
    with read_connection(db_path) as con:
        row = con.execute(
            "SELECT * FROM studios WHERE id = ?", (studio_pk,)
        ).fetchone()
    return _row_to_studio(row) if row else None


def get_studio_by_ref(
    db_path: Path, studio_id: int, studio_room_id: int
) -> Studio | None:
    with read_connection(db_path) as con:
        row = con.execute(
            "SELECT * FROM studios WHERE studio_id = ? AND studio_room_id = ?",
            (studio_id, studio_room_id),
        ).fetchone()
    return _row_to_studio(row) if row else None


def get_default_studio(db_path: Path) -> Studio | None:
    with read_connection(db_path) as con:
        row = con.execute(
            "SELECT * FROM studios WHERE is_default = 1 LIMIT 1"
        ).fetchone()
        if row is None:
            row = con.execute("SELECT * FROM studios ORDER BY id LIMIT 1").fetchone()
    return _row_to_studio(row) if row else None


def add_studio(
    db_path: Path,
    *,
    studio_id: int,
    studio_room_id: int,
    display_name: str,
    club_code: str | None = None,
    sisetcd: str | None = None,
    is_default: bool = False,
    sort_order: int = 0,
) -> None:
    with write_transaction(db_path) as con:
        con.execute(
            """
            INSERT INTO studios
              (studio_id, studio_room_id, display_name, club_code, sisetcd, is_default, sort_order)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(studio_id, studio_room_id) DO UPDATE SET
              display_name = excluded.display_name,
              club_code    = excluded.club_code,
              sisetcd      = excluded.sisetcd,
              sort_order   = excluded.sort_order
            """,
            (
                studio_id,
                studio_room_id,
                display_name,
                club_code,
                sisetcd,
                1 if is_default else 0,
                sort_order,
            ),
        )
