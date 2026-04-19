"""起動時に流す冪等なマイグレーション。

- スキーマバージョンが一致していれば何もしない
- 初回起動時に全テーブルを作り、既定店舗をシード
"""

from __future__ import annotations

import logging
from pathlib import Path

from db.connection import open_connection, write_transaction
from db.schema import (
    DEFAULT_STUDIO_SEEDS,
    INITIAL_SCHEMA,
    SCHEMA_VERSION,
    SEED_STUDIO_SQL,
    V3_SETUP,
    V4_SETUP,
    V5_SETUP,
    V6_SETUP,
)

logger = logging.getLogger(__name__)


def _current_version(db_path: Path) -> int:
    connection = open_connection(db_path)
    try:
        try:
            row = connection.execute(
                "SELECT MAX(version) AS v FROM schema_version"
            ).fetchone()
        except Exception:
            return 0
        return int(row["v"]) if row and row["v"] is not None else 0
    finally:
        connection.close()


V2_UPDATES: list[tuple[str, tuple]] = [
    # 既存店舗に club_code / sisetcd を埋める（公開月間 API 用）
    (
        "UPDATE studios SET club_code = ?, sisetcd = ? "
        "WHERE studio_id = ? AND studio_room_id = ? "
        "AND (club_code IS NULL OR club_code = '')",
        ("054", "A1", 79, 177),
    ),
]


def run_migrations(db_path: Path) -> None:
    """マイグレーションと初期シードを適用する。冪等。"""

    version = _current_version(db_path)
    if version >= SCHEMA_VERSION:
        logger.debug("schema already at version %d", version)
        return

    with write_transaction(db_path) as connection:
        if version < 1:
            for sql in INITIAL_SCHEMA:
                connection.execute(sql)
            for seed in DEFAULT_STUDIO_SEEDS:
                connection.execute(SEED_STUDIO_SQL, seed)
        if version < 2:
            for sql, params in V2_UPDATES:
                connection.execute(sql, params)
        if version < 3:
            for sql in V3_SETUP:
                connection.execute(sql)
        if version < 4:
            for sql in V4_SETUP:
                connection.execute(sql)
        if version < 5:
            for sql in V5_SETUP:
                connection.execute(sql)
        if version < 6:
            for sql in V6_SETUP:
                connection.execute(sql)
        connection.execute(
            "INSERT OR IGNORE INTO schema_version (version) VALUES (?)",
            (SCHEMA_VERSION,),
        )
    logger.info("applied schema version %d", SCHEMA_VERSION)
