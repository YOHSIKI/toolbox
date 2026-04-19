"""SQLite スキーマ定義。

- 全テーブルの DDL をここに集約（マイグレーションからのみ参照）
- 店舗・定期予約・予約・履歴・スケジュールキャッシュ・スキーマバージョン
"""

from __future__ import annotations

SCHEMA_VERSION = 6


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
    # ------------------------------------------------------
    # 予約予定（未開放の先のレッスンを、開放日の 9:00 に自動予約）
    # ------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS booking_intents (
        id TEXT PRIMARY KEY,
        lesson_date TEXT NOT NULL,
        lesson_time TEXT NOT NULL,
        program_id TEXT NOT NULL,
        program_name TEXT NOT NULL,
        studio_id INTEGER NOT NULL,
        studio_room_id INTEGER NOT NULL,
        seat_preferences TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        scheduled_run_at TEXT NOT NULL,
        executed_at TEXT,
        note TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_intents_run_at
        ON booking_intents (scheduled_run_at, status)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_intents_date
        ON booking_intents (lesson_date, status)
    """,
]


V3_SETUP: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS booking_intents (
        id TEXT PRIMARY KEY,
        lesson_date TEXT NOT NULL,
        lesson_time TEXT NOT NULL,
        program_id TEXT NOT NULL,
        program_name TEXT NOT NULL,
        studio_id INTEGER NOT NULL,
        studio_room_id INTEGER NOT NULL,
        seat_preferences TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        scheduled_run_at TEXT NOT NULL,
        executed_at TEXT,
        note TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_intents_run_at ON booking_intents (scheduled_run_at, status)",
    "CREATE INDEX IF NOT EXISTS idx_intents_date ON booking_intents (lesson_date, status)",
]


# ------------------------------------------------------------------
# V4: 座席レイアウトの永続化
# ------------------------------------------------------------------
# - space_details_cache: studio_room_space_id → 座席配置マスタ
#   （予約 API で観測した positions を JSON で保存。プロセス再起動後も復元可能）
# - space_layout_hints: (店舗, プログラム名, 曜日, 時刻) → 使用 space_id の学習 hint
#   （予約 API で観測したレッスンから蓄積、未開放週の lesson 補完に使う）
V4_SETUP: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS space_details_cache (
        studio_room_space_id INTEGER PRIMARY KEY,
        name TEXT,
        space_num INTEGER,
        grid_cols INTEGER NOT NULL DEFAULT 0,
        grid_rows INTEGER NOT NULL DEFAULT 0,
        positions_json TEXT NOT NULL,
        observed_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS space_layout_hints (
        studio_id INTEGER NOT NULL,
        studio_room_id INTEGER NOT NULL,
        program_name_norm TEXT NOT NULL,
        day_of_week INTEGER NOT NULL,
        start_time TEXT NOT NULL,
        studio_room_space_id INTEGER NOT NULL,
        program_name_raw TEXT,
        observed_at TEXT NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY (studio_id, studio_room_id, program_name_norm, day_of_week, start_time)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_layout_hints_name ON space_layout_hints (studio_id, studio_room_id, program_name_norm)",
    "CREATE INDEX IF NOT EXISTS idx_layout_hints_time ON space_layout_hints (studio_id, studio_room_id, day_of_week, start_time)",
]


# ------------------------------------------------------------------
# V5: reserve API で観測したレッスンの永続化
# ------------------------------------------------------------------
# - observed_lessons: (店舗, 部屋, 日付, 時刻, program_id) → 観測したプログラム名・
#   インストラクター名・studio_room_space_id。reserve API は「真の表示名」を返す
#   ので、これを蓄積し、後に公開月間 API 経由で組み立てる lesson に対して
#   program_name / instructor_name / studio_room_space_id を上書きする元にする。
V5_SETUP: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS observed_lessons (
        studio_id INTEGER NOT NULL,
        studio_room_id INTEGER NOT NULL,
        lesson_date TEXT NOT NULL,
        start_time TEXT NOT NULL,
        program_id TEXT NOT NULL,
        program_name TEXT,
        instructor_id INTEGER,
        instructor_name TEXT,
        studio_room_space_id INTEGER,
        capacity INTEGER,
        observed_at TEXT NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY (studio_id, studio_room_id, lesson_date, start_time, program_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_observed_lessons_date ON observed_lessons (studio_id, studio_room_id, lesson_date)",
    "CREATE INDEX IF NOT EXISTS idx_observed_lessons_name ON observed_lessons (studio_id, studio_room_id, program_name)",
]


# ------------------------------------------------------------------
# V6: 公開月間 API の progcd → reserve API の program_name/program_id の
# エイリアス学習テーブル
# ------------------------------------------------------------------
# 公開月間 API と reserve API の両方で観測できた枠について、
# `progcd` (hacomono 内部 ID; 例 "A0450" = ZUMBA(R), "AA756" = CSLive 系) を
# キーに reserve API 側の正しい表記を保存する。
# これにより、reserve API 窓の外にある未開放週の lesson でも、progcd が同じ
# であれば同じ正規表記で表示できる。progcd 単位なので誤置換の懸念は無い。
V6_SETUP: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS program_aliases (
        studio_id INTEGER NOT NULL,
        studio_room_id INTEGER NOT NULL,
        progcd TEXT NOT NULL,
        program_id TEXT,
        program_name TEXT NOT NULL,
        observed_at TEXT NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY (studio_id, studio_room_id, progcd)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_program_aliases_pid ON program_aliases (studio_id, studio_room_id, program_id)",
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
