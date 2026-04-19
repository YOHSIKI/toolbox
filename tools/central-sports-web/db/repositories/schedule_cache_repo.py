"""週次・月次スケジュールの TTL 付きキャッシュ。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from db.connection import read_connection, write_transaction
from db.repositories._common import format_datetime, parse_datetime


def put(
    db_path: Path,
    cache_key: str,
    source: str,
    payload: dict,
    ttl: timedelta | None,
) -> None:
    ttl_until = (datetime.now() + ttl) if ttl else None
    with write_transaction(db_path) as con:
        con.execute(
            """
            INSERT INTO schedule_cache (cache_key, source, fetched_at, ttl_until, payload)
            VALUES (?, ?, datetime('now'), ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                source     = excluded.source,
                fetched_at = excluded.fetched_at,
                ttl_until  = excluded.ttl_until,
                payload    = excluded.payload
            """,
            (
                cache_key,
                source,
                format_datetime(ttl_until) if ttl_until else None,
                json.dumps(payload, ensure_ascii=False),
            ),
        )


def get(db_path: Path, cache_key: str) -> dict | None:
    with read_connection(db_path) as con:
        row = con.execute(
            "SELECT * FROM schedule_cache WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
    if row is None:
        return None
    ttl = parse_datetime(row["ttl_until"])
    if ttl is not None and ttl < datetime.now():
        return None
    try:
        return json.loads(row["payload"])
    except json.JSONDecodeError:
        return None


def invalidate(db_path: Path, cache_key: str) -> None:
    with write_transaction(db_path) as con:
        con.execute(
            "DELETE FROM schedule_cache WHERE cache_key = ?",
            (cache_key,),
        )


def invalidate_prefix(db_path: Path, prefix: str) -> int:
    with write_transaction(db_path) as con:
        cur = con.execute(
            "DELETE FROM schedule_cache WHERE cache_key LIKE ?",
            (f"{prefix}%",),
        )
        return cur.rowcount
