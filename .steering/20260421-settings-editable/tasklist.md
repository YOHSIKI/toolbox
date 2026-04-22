# タスクリスト

## フェーズ 0: 準備

- [x] misfire_grace_time_seconds を settings_view.py から削除（済）
- [x] steering 作成（requirements / design / tasklist）

## フェーズ 1: Settings 拡張と参照元書き換え

- [x] `config/settings.py`
  - [x] 新フィールド追加: `alias_sim_accept`, `alias_sim_warn`, `history_keep_days`, `max_consecutive_failures`, `reserve_timeout_seconds`, `public_monthly_timeout_seconds`, `history_display_limit`, `schedule_refresh_hour/minute`, `my_reservations_sync_hour/minute`, `history_retention_dow`, `history_retention_hour`, `history_retention_minute`
  - [x] `misfire_grace_time_seconds` を削除
- [x] `app/utils/program_similarity.py` — `ALIAS_SIM_ACCEPT` / `ALIAS_SIM_WARN` 定数削除
- [x] `app/adapters/hacomono_gateway.py` — alias 判定で `settings.alias_sim_*` を使う（gateway `__init__` に注入）
- [x] `scheduler/jobs/retention.py` — `HISTORY_KEEP_DAYS` 削除、`settings.history_keep_days` を使う
- [x] `infra/hacomono/auth.py` — `MAX_CONSECUTIVE_FAILURES` 削除（`DEFAULT_MAX_CONSECUTIVE_FAILURES` にリネームしデフォルト値のみ残す）、`settings.max_consecutive_failures` を `AuthSession` の init で受ける
- [x] `infra/hacomono/http.py` — timeout デフォルトは現状維持、`app/deps.py` から `settings.reserve_timeout_seconds` を注入
- [x] `infra/hacomono/public_monthly.py` — 同上（`app/deps.py` から `settings.public_monthly_timeout_seconds` を注入）
- [x] `app/services/dashboard_query.py` — `limit=30` を `settings.history_display_limit` に、`ALIAS_SIM_ACCEPT` 参照も `self._settings.alias_sim_accept` に変更
- [x] `scheduler/runtime.py`
  - [x] cron trigger を settings から参照（schedule_refresh / my_reservations_sync / history_retention）
  - [x] `misfire_grace_time` を固定値 60 に（Settings 参照を削除）
- [x] ruff check が通る（編集対象ファイルに新規違反なし。既存ファイルに前から存在する警告のみ）

## フェーズ 2: app_settings テーブル

- [x] `db/schema.py`
  - [x] `SCHEMA_VERSION = 7`
  - [x] `V7_SETUP` に `app_settings` テーブル DDL
- [x] `db/migrations.py` — `if version < 7: V7_SETUP` 追加
- [x] `db/repositories/app_settings_repo.py` 新規
  - [x] `get_all(db_path) -> dict[str, str]`
  - [x] `upsert(db_path, key, value)`
  - [x] `delete(db_path, key)`

## フェーズ 3: override ローダと lifespan

- [x] `app/services/app_settings_loader.py` 新規
  - [x] `apply_db_overrides_to_env(db_path) -> int` で `os.environ["CSW_<KEY>"]` 注入
- [x] `app/lifespan.py`
  - [x] デフォルト db_path で先に `run_migrations`
  - [x] `apply_db_overrides_to_env` 呼び出し
  - [x] `get_settings.cache_clear()` でキャッシュ無効化
  - [x] `settings = get_settings()` 再取得
  - [x] ログに "applied N overrides" を出す

## フェーズ 4: settings_view.py に editable 属性

- [x] `SettingItem` に `editable`, `edit_key`, `edit_type`, `edit_min`, `edit_max`, `edit_step`, `edit_options`, `current_raw` 追加
- [x] 各ビルダ関数で、編集可項目に `editable=True` + 属性を付与
- [x] 時刻系は 2 フィールドを HH:MM に合成した `current_raw` を生成
- [x] 出典（SettingSource）の行番号を実コードに合わせて更新

## フェーズ 5: POST /update ルート + バリデーション

- [x] `app/routes/settings.py`
  - [x] `@router.post("/reserve/settings/update", name="settings_update")` 追加
  - [x] `validate_setting(key, value)` でバリデーション（design の表に従う）
  - [x] 時刻は `<key>_hour` / `<key>_minute` の 2 レコードに分解して upsert
  - [x] 整合性制約: `alias_sim_warn < alias_sim_accept`, `calendar_start_time < calendar_end_time`
  - [x] 成功時 `RedirectResponse("/reserve/settings?saved=1", 303)`
  - [x] エラー時 303 redirect + `?error=...` 付きでフォーム再描画

## フェーズ 6: テンプレート編集化

- [x] `ui/templates/settings.html`
  - [x] 画面冒頭に `?saved=1` ならバナー「保存しました。次回起動で反映されます。」
  - [x] `?error=` があれば赤バナー
  - [x] `{% if item.editable %}` で input を出す、そうでなければ従来通り `value_display` を表示
  - [x] 各行に `<form method="post">` + hidden key + value input + 保存ボタン（項目単位の粒度）
  - [x] 編集不可項目の見た目は変えない

## フェーズ 7: 静的チェック

- [x] `ruff check` — 今回の編集・新規ファイルに新規違反なし。既存の I001/UP037 は変更前から存在していた
- [x] `grep "ALIAS_SIM_ACCEPT\|ALIAS_SIM_WARN\|HISTORY_KEEP_DAYS\|MAX_CONSECUTIVE_FAILURES\|misfire_grace_time_seconds"` — 残置は以下のみで OK:
  - `scripts/verify_similarity.py` — コメント・print 内の文字列言及のみ
  - `infra/hacomono/auth.py` — `DEFAULT_MAX_CONSECUTIVE_FAILURES` 定数（設計通り、default 値として残す）
  - `config/settings.py` — `misfire_grace_time_seconds` は削除済みを示すコメント行のみ

## フェーズ 8: ビルド & デプロイ

- [ ] `gateway docker --project central-sports-web compose build web`
- [ ] `gateway docker --project central-sports-web compose up -d web`
- [ ] ログに "applied N overrides from app_settings" が出ること（N=0 初回）
- [ ] エラーなく起動

## フェーズ 9: 動作確認（スクリーンショット）

- [ ] chromium で `/reserve/settings` 表示
- [ ] misfire 項目が消えている
- [ ] 編集可項目にすべて input が出ている
- [ ] `alias_sim_accept` を 0.65 にして保存 → `?saved=1` バナー表示
- [ ] コンテナ再起動 → 画面の値が 0.65 になる
- [ ] 不整合（warn > accept）にしようとすると 400 + エラーバナー
- [ ] 編集不可項目（店舗）は input が無い

## フェーズ 10: 振り返り

- [ ] 計画と実績の差分を記録
- [ ] Issue #1 本文（または末尾コメント）に Phase 2 完了状況を追記
