# 設計書

## アーキテクチャ概要

編集可項目を **すべて Settings 属性に吸い上げ**、DB テーブル `app_settings` を `os.environ` 経由で override レイヤーとする。pydantic の env_prefix="CSW_" を活かせば、DB override と .env が同じ仕組みで扱える。

```
起動時:
  run_migrations(db_path)                      # app_settings テーブル保証
  load_overrides(db_path) → os.environ に注入  # DB 値を env に
  Settings() 構築                              # pydantic が env/DB ファイルから読む
  build_context(settings)
  start_scheduler(context) if scheduler_enabled

編集時:
  POST /reserve/settings/update {key, value}
    → validate
    → app_settings_repo.upsert(key, value)
    → 302 redirect to /reserve/settings with "saved" banner
    → Settings オブジェクト自体は書き換えない
```

## 優先順位

DB override > .env > Settings クラスのデフォルト。

pydantic-settings のデフォルト挙動は「環境変数 > env_file > default」なので、**DB から読んだ値を `os.environ` に書き込めば自動的に env_file より優先される**。追加コードは注入箇所だけで済む。

## コンポーネント設計

### 1. `db/schema.py` に V7_SETUP 追加

```python
V7_SETUP: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS app_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
]
```

`SCHEMA_VERSION = 7`。

### 2. `db/repositories/app_settings_repo.py` 新規

```python
def get_all(db_path: Path) -> dict[str, str]: ...
def upsert(db_path: Path, key: str, value: str) -> None: ...
def delete(db_path: Path, key: str) -> None: ...
```

### 3. `app/services/app_settings_loader.py` 新規

`Settings()` 構築の前に呼ぶ。

```python
def apply_db_overrides_to_env(db_path: Path) -> int:
    """DB の app_settings を os.environ に注入して Settings 上書きに使う。

    返り値: 注入したキー数。
    """
    items = app_settings_repo.get_all(db_path)
    for key, value in items.items():
        env_name = f"CSW_{key.upper()}"
        os.environ[env_name] = value
    return len(items)
```

### 4. `config/settings.py` のフィールド追加

既存に加えて以下を追加（すべて `CSW_<UPPER>` 環境変数で上書き可）:

```python
# 追加
alias_sim_accept: float = 0.60
alias_sim_warn: float = 0.40
history_keep_days: int = 90
max_consecutive_failures: int = 3
reserve_timeout_seconds: float = 15.0
public_monthly_timeout_seconds: float = 10.0
history_display_limit: int = 30

# スケジューラ (ハードコード Cron を吸い上げ)
schedule_refresh_hour: int = 0
schedule_refresh_minute: int = 5
my_reservations_sync_hour: int = 0
my_reservations_sync_minute: int = 0
history_retention_dow: str = "sun"
history_retention_hour: int = 4
history_retention_minute: int = 0

# 削除
# misfire_grace_time_seconds: int = 120  ← 削除
```

### 5. モジュール定数→Settings 参照への書き換え

| 参照元 | 旧 | 新 |
|---|---|---|
| `app/utils/program_similarity.py` | `ALIAS_SIM_ACCEPT = 0.60` / `ALIAS_SIM_WARN = 0.40` | ここから削除。`hacomono_gateway` 等は `settings.alias_sim_accept/warn` を直接参照 |
| `scheduler/jobs/retention.py` | `HISTORY_KEEP_DAYS = 90` | `settings.history_keep_days` |
| `infra/hacomono/auth.py` | `MAX_CONSECUTIVE_FAILURES = 3` | ここから削除。`settings.max_consecutive_failures` を auth クラスの `__init__` で受ける |
| `infra/hacomono/http.py` | `timeout: float = 15.0` | 呼び出し側が `settings.reserve_timeout_seconds` を渡す |
| `infra/hacomono/public_monthly.py` | `timeout: float = 10.0` | 呼び出し側が `settings.public_monthly_timeout_seconds` を渡す |
| `app/services/dashboard_query.py` | `limit=30` ハードコード | `settings.history_display_limit` |
| `scheduler/runtime.py` | `CronTrigger(hour=0, minute=5)` 等 | Settings の値を参照 |

**注意**: 移行で「参照する箇所」が散らばっているため、Agent に依頼するときは **grep で全使用箇所を洗い出してから書き換え** を指示する。既存の挙動を壊さないこと。

設定画面の出典（`SettingSource(file, line)`）は、書き換え後の実際の行番号に合わせて更新する。

### 6. `app/lifespan.py` で override 適用

`Settings()` を取る前に DB を初期化して override を注入。

```python
from pathlib import Path
from config.settings import BASE_DIR  # or compute
from db.migrations import run_migrations
from app.services.app_settings_loader import apply_db_overrides_to_env

# 1. マイグレーション（app_settings テーブル含む）を先に走らせる
#    ただし db_path は Settings を読まないと不明 → default を先に使って migrate
default_db = BASE_DIR / "data" / "app.db"
run_migrations(default_db)

# 2. override を env に注入
injected = apply_db_overrides_to_env(default_db)
logger.info("applied %d overrides from app_settings", injected)

# 3. Settings 構築（env 経由で override が効く）
get_settings.cache_clear()  # lru_cache 無効化
settings = get_settings()
```

**懸念**: `get_settings()` は `@lru_cache` 済みなので、1 度呼ばれていると env 注入が効かない。`lifespan` の最初のほうで注入してから `get_settings.cache_clear()` を呼ぶ。`app.main` モジュール最上部の `get_settings()` 呼び出しを移すか、cache_clear を徹底する。

### 7. `app/services/settings_view.py` の拡張

```python
@dataclass(frozen=True)
class SettingItem:
    label: str
    param_name: str
    value_display: str
    note: str | None
    source: SettingSource
    # Phase 2 追加
    editable: bool = False
    edit_key: str | None = None          # app_settings のキー（例 "auto_booking_time"）
    edit_type: str = "text"              # "text" | "number" | "time" | "toggle" | "select"
    edit_min: float | None = None
    edit_max: float | None = None
    edit_step: float | None = None
    edit_options: list[str] | None = None  # select 用
    current_raw: str | None = None       # input の value 属性用（HH:MM など）
```

ビルダ関数で各項目に `editable=True, edit_key=..., edit_type=...` を付与する。時刻は 2 フィールド (hour/minute) を HH:MM に合成して `current_raw` に入れる。

### 8. `app/routes/settings.py` に POST 追加

```python
@router.post("/reserve/settings/update", name="settings_update")
def settings_update(
    request: Request,
    context: AppContext = Depends(get_context),
    key: str = Form(...),
    value: str = Form(...),
):
    # validate
    validated = validate_setting(key, value)
    if validated.error:
        # 400 でフォーム再描画
        ...
    # 時刻は HH:MM → hour/minute に分解して 2 キー保存
    for db_key, db_value in validated.db_items:
        app_settings_repo.upsert(context.db_path, db_key, db_value)
    # redirect with flash
    return RedirectResponse("/reserve/settings?saved=1", status_code=303)
```

**バリデーション表**（`validate_setting`）:

| key | type | min/max | 備考 |
|---|---|---|---|
| `scheduler_enabled` | toggle | — | "true"/"false" |
| `login_warmup_time` | time | — | HH:MM → `login_warmup_hour` + `login_warmup_minute` 2 キー保存 |
| `auto_booking_time` | time | — | 同上 |
| `schedule_refresh_time` | time | — | 同上 |
| `my_reservations_sync_time` | time | — | 同上 |
| `history_retention_dow` | select | {sun,mon,…,sat} | |
| `history_retention_time` | time | — | → `history_retention_hour` + `history_retention_minute` |
| `calendar_start_time` | int | 0-23 | |
| `calendar_end_time` | int | 0-23 | start < end を強制 |
| `history_display_limit` | int | 1-200 | |
| `alias_sim_accept` | float | 0-1 | warn < accept を強制 |
| `alias_sim_warn` | float | 0-1 | 同上 |
| `reserve_timeout_seconds` | float | 1-60 | |
| `public_monthly_timeout_seconds` | float | 1-60 | |
| `max_consecutive_failures` | int | 1-10 | |
| `history_keep_days` | int | 1-365 | |

### 9. `ui/templates/settings.html` の編集化

- `{% if item.editable %}` で input を出す、そうでなければ従来通り `value_display` を出す
- input の `name="value"`, `hidden name="key" value="{{ item.edit_key }}"` で POST
- セクションごとに `<form method="post" action="{{ url_for('settings_update') }}">` で項目単位の保存ボタン（1 行 1 ボタン or セクション 1 ボタン、UI 判断）
- 画面冒頭に `{% if request.query_params.get('saved') %}` で「保存しました。次回起動で反映されます。」バナー

### 10. `scheduler/runtime.py` の値参照書き換え

```python
scheduler.add_job(
    cache_refresh_job,
    trigger=CronTrigger(
        hour=settings.schedule_refresh_hour,
        minute=settings.schedule_refresh_minute,
    ),
    ...
)
scheduler.add_job(
    daily_sync_job,
    trigger=CronTrigger(
        hour=settings.my_reservations_sync_hour,
        minute=settings.my_reservations_sync_minute,
    ),
    ...
)
scheduler.add_job(
    retention_job,
    trigger=CronTrigger(
        day_of_week=settings.history_retention_dow,
        hour=settings.history_retention_hour,
        minute=settings.history_retention_minute,
    ),
    ...
)

# job_defaults の misfire_grace_time は固定値 60 に（設定削除）
job_defaults={
    "coalesce": True,
    "max_instances": 1,
    "misfire_grace_time": 60,
},
```

## データフロー

```
[編集]
Browser --POST /reserve/settings/update {key, value}--> settings_route
  → validate_setting(key, value)
  → app_settings_repo.upsert(db, "<db_key>", "<db_value>")  # 時刻は 2 キー分
  → redirect to /reserve/settings?saved=1
Browser ← 画面再描画（保存済みバナー + 現在値は app_settings 未反映の旧値。次回起動で反映される旨を明示）

[起動時]
lifespan → run_migrations(db) → apply_db_overrides_to_env(db) → Settings()
  Settings.alias_sim_accept などは env 経由で DB 値を使う
  scheduler は Settings から時刻を読んで cron 登録
```

## エラーハンドリング

- バリデーションエラー: 400、対象セクションのトップに赤バナー、対象 input に赤枠と下部に理由
- DB 書き込みエラー: 500（FastAPI 標準ハンドラ）
- 起動時 override 読込失敗: WARN ログを出して従来の env/default で起動（継続性優先）

## テスト方針

- ユニットテスト追加しない（既存の方針踏襲）
- 動作確認（必須）:
  1. misfire 項目が画面から消えている
  2. 各セクションの編集可項目に input が出ている
  3. `alias_sim_accept` を 0.65 に変更 → 保存 → DB に `alias_sim_accept=0.65` が入る
  4. コンテナ再起動 → 画面の表示値が 0.65 に更新される
  5. `alias_sim_accept < alias_sim_warn` にしようとするとエラー
  6. `history_retention_dow` を `mon` に変更 → 再起動 → `scheduler.get_jobs()` で月曜 cron になる
  7. 編集不可項目（店舗）に input が出ないこと

## ファイル構成

**新規**:
- `db/repositories/app_settings_repo.py`
- `app/services/app_settings_loader.py`

**変更**:
- `db/schema.py`（V7_SETUP 追加、SCHEMA_VERSION=7）
- `db/migrations.py`（V7 適用）
- `config/settings.py`（新フィールド + misfire 削除）
- `app/lifespan.py`（override 注入）
- `app/services/settings_view.py`（editable 属性、misfire 削除、新フィールドの表示追加）
- `app/routes/settings.py`（POST /update）
- `ui/templates/settings.html`（input 化）
- `scheduler/runtime.py`（設定値参照、misfire 固定値）
- `app/utils/program_similarity.py`（ALIAS_SIM_* 定数削除）
- `scheduler/jobs/retention.py`（`HISTORY_KEEP_DAYS` 削除、settings 経由）
- `infra/hacomono/auth.py`（`MAX_CONSECUTIVE_FAILURES` 削除、settings 経由）
- `infra/hacomono/http.py`（timeout デフォルト → settings 注入）
- `infra/hacomono/public_monthly.py`（同上）
- `app/adapters/hacomono_gateway.py`（alias 判定で settings.alias_sim_* を使う）
- `app/services/dashboard_query.py`（limit=30 → settings.history_display_limit）

## 実装の順序

1. Settings にフィールド追加 + 旧モジュール定数削除 + 参照元書き換え（最も広範囲、静的チェックで網羅確認）
2. `app_settings` テーブル + repository + loader
3. `lifespan` で override 注入 (`get_settings.cache_clear()` 徹底)
4. `settings_view.py` に editable 属性追加、misfire 削除
5. POST /update ルート + バリデーション
6. template を input 化 + 保存バナー
7. scheduler の cron を設定参照に変更 + misfire 固定値
8. ruff check
9. ビルド + compose up -d
10. chromium でスクショ（編集→保存→再起動→反映の動線）
