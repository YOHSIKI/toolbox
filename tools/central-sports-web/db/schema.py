"""SQLite スキーマ定義。

- 全テーブルの DDL をここに集約（マイグレーションからのみ参照）
- 店舗・定期予約・予約・履歴・スケジュールキャッシュ・スキーマバージョン
"""

from __future__ import annotations

SCHEMA_VERSION = 2


INITIAL_SCHEMA = [
    # ------------------------------------------------------
    # 店舗マスター
    # ------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS studios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        studio_id INTEGER NOT NULL,
        studio_room_id INTEGER NOT NULL,
        display_name TEXT NOT NULL,
        club_code TEXT,
        sisetcd TEXT,
        is_default INTEGER NOT NULL DEFAULT 0,
        sort_order INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(studio_id, studio_room_id)
    )
    """,
    # ------------------------------------------------------
    # 定期予約
    # ------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS recurring_reservations (
        id TEXT PRIMARY KEY,
        day_of_week INTEGER NOT NULL,
        start_time TEXT NOT NULL,
        program_id TEXT NOT NULL,
        program_name TEXT NOT NULL,
        studio_id INTEGER NOT NULL,
        studio_room_id INTEGER NOT NULL,
        seat_preferences TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active',
        note TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_recurring_status
        ON recurring_reservations (status)
    """,
    # ------------------------------------------------------
    # 予約（ローカル記録、hacomono の予約に対応）
    # ------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS reservations (
        id TEXT PRIMARY KEY,
        external_id INTEGER,
        studio_lesson_id INTEGER,
        lesson_date TEXT NOT NULL,
        lesson_time TEXT NOT NULL,
        program_id TEXT NOT NULL,
        program_name TEXT NOT NULL,
        instructor_name TEXT,
        studio_id INTEGER NOT NULL,
        studio_room_id INTEGER NOT NULL,
        seat_no INTEGER,
        origin TEXT NOT NULL DEFAULT 'single',
        origin_id TEXT,
        status TEXT NOT NULL DEFAULT 'confirmed',
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_reservations_external
        ON reservations (external_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_reservations_date
        ON reservations (lesson_date, lesson_time)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_reservations_origin
        ON reservations (origin, origin_id)
    """,
    # ------------------------------------------------------
    # 実行履歴
    # ------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id TEXT NOT NULL,
        occurred_at TEXT NOT NULL DEFAULT (datetime('now')),
        category TEXT NOT NULL,
        endpoint TEXT,
        elapsed_ms INTEGER,
        result TEXT NOT NULL,
        message TEXT,
        metadata TEXT
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_history_occurred
        ON history (occurred_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_history_category
        ON history (category, occurred_at DESC)
    """,
    # ------------------------------------------------------
    # スケジュールキャッシュ
    # ------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS schedule_cache (
        cache_key TEXT PRIMARY KEY,
        source TEXT NOT NULL,
        fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
        ttl_until TEXT,
        payload TEXT NOT NULL
    )
    """,
    # ------------------------------------------------------
    # スキーマバージョン管理
    # ------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS schema_version (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
]


SEED_STUDIO_SQL = (
    "INSERT OR IGNORE INTO studios "
    "(studio_id, studio_room_id, display_name, club_code, sisetcd, is_default, sort_order) "
    "VALUES (?, ?, ?, ?, ?, ?, ?)"
)


# 既定店舗のシード値。public monthly API に必要な club_code / sisetcd も埋める
DEFAULT_STUDIO_SEEDS: list[tuple[int, int, str, str | None, str | None, int, int]] = [
    (79, 177, "セントラルフィットネスクラブ府中", "054", "A1", 1, 0),
]
