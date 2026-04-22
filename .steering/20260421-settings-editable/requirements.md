# 要求内容

## 概要

`/reserve/settings` を **閲覧専用から編集可能** にする。Phase 1 で表示していた各項目のうち、運用で値を変えたいものを UI から編集できるようにし、変更は **次回起動で反映** する方式を採る。hot reload はしない。

## 背景

- Phase 1 では「実行中の値を出典付きで並べる」read-only の画面を作った（`.steering/20260420-settings-page/`）。
- Issue #1 の末尾コメント「設定画面 Phase 1 整理状況メモ（2026-04-21）」で Phase 2 が残課題として明記されている。
- 2026-04-21 の対話で **misfire_grace_time_seconds を削除する／編集 UI を作る** 方針が確定した（「ジョブ発火遅延の許容秒数とかさ、なんかいらんって言ってたやつが残ってるままなんですけど、ちゃんと決めた仕様通りにやれよ」）。

## スコープ

### 削除（UI からも Settings からも消す）

- `misfire_grace_time_seconds`（pydantic 設定、ジョブ `misfire_grace_time` に使っているが運用で触る価値なし。scheduler デフォルトの 60 秒で十分）

### 編集可（UI からフォーム入力 → 次回起動で反映）

| セクション | 項目 | 種別 | 備考 |
|---|---|---|---|
| スケジューラ | `scheduler_enabled` | toggle | false で全ジョブ停止 |
| スケジューラ | `login_warmup_time` | HH:MM | 既存 `login_warmup_hour` + `login_warmup_minute` を 1 フィールド化 |
| スケジューラ | `auto_booking_time` | HH:MM | 同上 (`auto_booking_hour/minute`) |
| スケジューラ | `schedule_refresh_time` | HH:MM | 現状ハードコード (00:05)、Settings に吸い上げ |
| スケジューラ | `my_reservations_sync_time` | HH:MM | 現状ハードコード (00:00)、Settings に吸い上げ |
| スケジューラ | `history_retention_dow` | select | sun/mon/…/sat |
| スケジューラ | `history_retention_time` | HH:MM | 現状ハードコード (日曜 04:00) |
| 表示 | `calendar_start_time` | number (0-23) | |
| 表示 | `calendar_end_time` | number (0-23) | |
| 表示 | `history_display_limit` | number (1-200) | 現状 `dashboard_query.py` にハードコード 30、Settings に吸い上げ |
| プログラム名の一致判定 | `alias_sim_accept` | number (0-1, step 0.01) | 現状モジュール定数、Settings に吸い上げ |
| プログラム名の一致判定 | `alias_sim_warn` | number (0-1, step 0.01) | 同上 |
| 接続・タイムアウト | `reserve_timeout_seconds` | number (1-60) | `infra/hacomono/http.py` のデフォルト値、Settings に吸い上げ |
| 接続・タイムアウト | `public_monthly_timeout_seconds` | number (1-60) | 同上 (`public_monthly.py`) |
| 接続・タイムアウト | `max_consecutive_failures` | number (1-10) | `infra/hacomono/auth.py` のモジュール定数、Settings に吸い上げ |
| 保持期間 | `history_keep_days` | number (1-365) | `scheduler/jobs/retention.py` のモジュール定数、Settings に吸い上げ |

### 表示のみ（編集不可）

- `default_studio_id` / `default_studio_room_id` / `default_studio_name` — 店舗切替はトップバーのプルダウンでやるのでデフォルト値は固定でよい。
- 秘匿情報（Basic 認証・Discord Webhook・secrets 系）— 従来通り非表示。

## 動作仕様

### 保存フロー

1. ユーザーが項目右の input に値を入力し、セクション末尾の「このセクションを保存」ボタンを押す（または項目ごとに保存する粒度でも可）
2. サーバーは値のバリデーションをして `app_settings` テーブルに upsert（既存キーなら更新、なければ挿入）
3. 保存後のレスポンスには「次回起動で反映されます」バッジを出す
4. 即時には Settings は書き換えない（race や整合性のリスクを取らない）

### 起動時の反映

1. `run_migrations()` で `app_settings` テーブル存在を保証
2. `Settings()` 構築前に `app_settings` から全レコードを読み、`os.environ["CSW_<KEY>"]` に注入
3. pydantic が env 経由で値を読む（既存の .env より **DB 優先**）
4. `lifespan` ログに `"settings override from app_settings: N keys"` を出す

### バリデーション

- HH:MM: 正規表現 `^\d{2}:\d{2}$` + 範囲 (hour 0-23, minute 0-59)
- number: min/max を守る
- 範囲外は 400 エラーでフォーム再描画、エラーメッセージを項目の下に赤字表示
- toggle: `true`/`false` 文字列

### UI

- 既存の 5 列レイアウト（項目名 / パラメータ名 / 値 / 説明 / 出典）を維持
- 「値」列を input に置き換える（編集可項目のみ）
- セクションごとに「変更を保存」ボタンを末尾に置く
- 編集不可項目は従来通り表示のみ
- 保存成功時はトースト or バナーで「次回起動で反映されます」表示
- 既存の HTMX 部分更新は維持

## スコープ外（将来の Phase 3 候補）

- hot reload（Cron 時刻変更後に `scheduler.reschedule_job` を呼ぶ）
- 設定値の変更履歴・監査ログ
- デフォルト店舗の変更 UI（現状のプルダウンで充分）
- Basic 認証ユーザー・パスワード（secret-manager 管轄）

## 参照

- `.steering/20260420-settings-page/` — Phase 1 steering（完了）
- Issue #1 末尾コメント「設定画面 Phase 1 整理状況メモ（2026-04-21）」
- `tools/central-sports-web/app/services/settings_view.py` — 現行ビュー
- `tools/central-sports-web/config/settings.py` — Settings 本体
