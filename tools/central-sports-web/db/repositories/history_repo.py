"""実行履歴の読み書き。"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from app.domain.entities import HistoryCategory, HistoryEntry, HistoryResult
from db.connection import read_connection, write_transaction
from db.repositories._common import format_datetime, parse_datetime


def _row_to_entry(row: sqlite3.Row) -> HistoryEntry:
    meta_raw = row["metadata"]
    metadata = json.loads(meta_raw) if meta_raw else None
    return HistoryEntry(
        id=row["id"],
        request_id=row["request_id"],
        occurred_at=parse_datetime(row["occurred_at"]) or datetime.now(),
        category=HistoryCategory(row["category"]),
        endpoint=row["endpoint"],
        elapsed_ms=row["elapsed_ms"],
        result=HistoryResult(row["result"]),
        message=row["message"],
        metadata=metadata,
    )


def insert(db_path: Path, entry: HistoryEntry) -> None:
    with write_transaction(db_path) as con:
        con.execute(
            """
            INSERT INTO history
              (request_id, occurred_at, category, endpoint, elapsed_ms, result, message, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry.request_id,
                format_datetime(entry.occurred_at),
                entry.category.value,
                entry.endpoint,
                entry.elapsed_ms,
                entry.result.value,
                entry.message,
                json.dumps(entry.metadata, ensure_ascii=False) if entry.metadata else None,
            ),
        )


def list_recent(
    db_path: Path,
    *,
    limit: int = 50,
    categories: list[HistoryCategory] | None = None,
) -> list[HistoryEntry]:
    sql = "SELECT * FROM history"
    params: list[Any] = []
    if categories:
        placeholders = ",".join("?" * len(categories))
        sql += f" WHERE category IN ({placeholders})"
        params.extend(c.value for c in categories)
    sql += " ORDER BY occurred_at DESC LIMIT ?"
    params.append(limit)
    with read_connection(db_path) as con:
        rows = con.execute(sql, tuple(params)).fetchall()
    return [_row_to_entry(r) for r in rows]


def list_between(
    db_path: Path,
    *,
    start: datetime,
    end: datetime,
    categories: list[HistoryCategory] | None = None,
) -> list[HistoryEntry]:
    sql = "SELECT * FROM history WHERE occurred_at BETWEEN ? AND ?"
    params: list[Any] = [format_datetime(start), format_datetime(end)]
    if categories:
        placeholders = ",".join("?" * len(categories))
        sql += f" AND category IN ({placeholders})"
        params.extend(c.value for c in categories)
    sql += " ORDER BY occurred_at DESC"
    with read_connection(db_path) as con:
        rows = con.execute(sql, tuple(params)).fetchall()
    return [_row_to_entry(r) for r in rows]


def purge_older_than(db_path: Path, cutoff: datetime) -> int:
    with write_transaction(db_path) as con:
        cur = con.execute(
            "DELETE FROM history WHERE occurred_at < ?",
            (format_datetime(cutoff),),
        )
        return cur.rowcount


__all__ = ["insert", "list_recent", "list_between", "purge_older_than"]
