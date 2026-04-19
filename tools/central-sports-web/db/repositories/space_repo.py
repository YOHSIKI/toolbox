"""座席レイアウトと学習 hint の永続化リポジトリ。

- `space_details_cache`: studio_room_space_id → 座席配置（positions + grid 寸法）
  reserve API で観測した配置を JSON で保存。起動時にメモリに復元して、
  fetch_seat_map から素早く引ける。
- `space_layout_hints`: (studio, program_name, weekday, start_time) →
  studio_room_space_id の学習 hint。公開月間 API 経由の lesson（space_id 不明）に
  対して、この索引で補完する。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from app.domain.entities import SeatPosition
from db.connection import read_connection, write_transaction


# ---------------------------------------------------------------
# space_details_cache（座席配置マスタのローカルキャッシュ）
# ---------------------------------------------------------------

def upsert_space_details(
    db_path: Path,
    *,
    studio_room_space_id: int,
    name: str | None,
    space_num: int | None,
    grid_cols: int,
    grid_rows: int,
    positions: list[SeatPosition],
) -> None:
    """座席配置をローカル DB に保存（既存なら上書き）。"""

    positions_json = json.dumps(
        [
            {"no": p.no, "no_label": p.no_label, "coord_x": p.coord_x, "coord_y": p.coord_y}
            for p in positions
        ],
        ensure_ascii=False,
    )
    with write_transaction(db_path) as con:
        con.execute(
            """
            INSERT INTO space_details_cache
                (studio_room_space_id, name, space_num, grid_cols, grid_rows,
                 positions_json, observed_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(studio_room_space_id) DO UPDATE SET
                name = excluded.name,
                space_num = excluded.space_num,
                grid_cols = excluded.grid_cols,
                grid_rows = excluded.grid_rows,
                positions_json = excluded.positions_json,
                observed_at = excluded.observed_at
            """,
            (
                studio_room_space_id,
                name,
                space_num,
                grid_cols,
                grid_rows,
                positions_json,
            ),
        )


def list_space_details(db_path: Path) -> dict[int, dict]:
    """全 space の配置を読み込む。起動時のメモリ復元で使う。

    Returns: {space_id: {"name", "space_num", "grid_cols", "grid_rows",
                         "positions": list[SeatPosition]}}
    """

    result: dict[int, dict] = {}
    with read_connection(db_path) as con:
        rows = con.execute(
            """
            SELECT studio_room_space_id, name, space_num, grid_cols, grid_rows, positions_json
            FROM space_details_cache
            """
        ).fetchall()
    for row in rows:
        positions = _decode_positions(row["positions_json"])
        result[int(row["studio_room_space_id"])] = {
            "name": row["name"],
            "space_num": row["space_num"],
            "grid_cols": int(row["grid_cols"] or 0),
            "grid_rows": int(row["grid_rows"] or 0),
            "positions": positions,
        }
    return result


def _decode_positions(raw: str | None) -> list[SeatPosition]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    out: list[SeatPosition] = []
    if not isinstance(data, list):
        return out
    for d in data:
        if not isinstance(d, dict):
            continue
        try:
            out.append(
                SeatPosition(
                    no=int(d["no"]),
                    no_label=str(d.get("no_label") or d["no"]),
                    coord_x=int(d["coord_x"]),
                    coord_y=int(d["coord_y"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return out


# ---------------------------------------------------------------
# space_layout_hints（学習 hint）
# ---------------------------------------------------------------

def upsert_layout_hint(
    db_path: Path,
    *,
    studio_id: int,
    studio_room_id: int,
    program_name_norm: str,
    program_name_raw: str | None,
    day_of_week: int,
    start_time: str,
    studio_room_space_id: int,
) -> None:
    """(店舗, プログラム名, 曜日, 時刻) → space_id の学習 hint を upsert。"""

    with write_transaction(db_path) as con:
        con.execute(
            """
            INSERT INTO space_layout_hints
                (studio_id, studio_room_id, program_name_norm, day_of_week, start_time,
                 studio_room_space_id, program_name_raw, observed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(studio_id, studio_room_id, program_name_norm, day_of_week, start_time)
            DO UPDATE SET
                studio_room_space_id = excluded.studio_room_space_id,
                program_name_raw = excluded.program_name_raw,
                observed_at = excluded.observed_at
            """,
            (
                studio_id,
                studio_room_id,
                program_name_norm,
                day_of_week,
                start_time,
                studio_room_space_id,
                program_name_raw,
            ),
        )


def list_layout_hints(
    db_path: Path,
    *,
    studio_id: int | None = None,
    studio_room_id: int | None = None,
) -> list[sqlite3.Row]:
    """学習 hint 一覧。起動時のメモリ復元で使う。"""

    sql = "SELECT * FROM space_layout_hints WHERE 1=1"
    params: list = []
    if studio_id is not None:
        sql += " AND studio_id = ?"
        params.append(studio_id)
    if studio_room_id is not None:
        sql += " AND studio_room_id = ?"
        params.append(studio_room_id)
    with read_connection(db_path) as con:
        return con.execute(sql, tuple(params)).fetchall()


__all__ = [
    "upsert_space_details",
    "list_space_details",
    "upsert_layout_hint",
    "list_layout_hints",
]
