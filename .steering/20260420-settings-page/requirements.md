# 要求内容

## 概要

central-sports-web に **閲覧専用の設定画面**（`GET /reserve/settings`）を追加する。アプリ全体の挙動を決める各種パラメータ（スケジューラ時刻、キャッシュ TTL、類似度しきい値、タイムアウト等）を、出典ファイル・行番号付きで一覧表示するだけのビューを提供する。

編集機能は今回のスコープ外（Phase 2 以降）。

## 背景

- 稼働中のアプリ挙動を決めている「数値」がコード全域に点在している。
  - `config/settings.py`（pydantic Settings、20 項目以上）
  - `scheduler/runtime.py`（scheduler のジョブ時刻、cron 定義）
  - `scheduler/jobs/retention.py`（履歴保持日数 `HISTORY_KEEP_DAYS = 90`）
  - `app/adapters/hacomono_gateway.py`（`_cache_ttl = 86400.0`、`_ALIAS_SIM_ACCEPT` / `_ALIAS_SIM_WARN`）
  - `app/services/sync_my_reservations.py`（`DEFAULT_INTERVAL_SEC = 86400.0`）
  - `app/services/dashboard_query.py`（配置プレビュー週数 `weeks=4`、履歴表示件数 `limit=30`）
  - `infra/hacomono/http.py`（reserve API timeout `15.0`）
  - `infra/hacomono/public_monthly.py`（公開月間 API timeout `10.0`）
  - `infra/hacomono/auth.py`（`MAX_CONSECUTIVE_FAILURES = 3`）
- 「今この瞬間、何分で何が動くのか」「類似度しきい値はいくつか」を知りたいとき、毎回コードを grep する必要がある。
- Issue #1（central-sports-web 本体の傘 Issue）で設定画面が残課題として挙がっていた。
- 本日（2026-04-20）の対話で、**Phase 1 として「まず全項目を出典付きで見られる画面」を先に作る**判断をした。編集機能（Phase 2）と hot reload（Phase 3）はそれぞれ独立した検討事項として後続する。

## 実装対象

### 1. 設定ページ本体（`GET /reserve/settings`）

basic_auth 配下で、ログイン済みユーザーが閲覧できる read-only な HTML ページ。既存の `/reserve/recurring` と同じナビゲーション配下に置き、サイドバーから1クリックで遷移できる。

受入条件:
- [ ] `GET /reserve/settings` が 200 OK で HTML を返す
- [ ] basic_auth が無効化されていない環境では未認証アクセスが 401 になる
- [ ] サイドバーに「設定」メニューが現れ、クリックで遷移できる（HTMX 部分更新も動く）
- [ ] 画面は9つのセクション（後述）に分かれており、各項目の右側に「出典ファイル:行番号」が小さく表示される

### 2. セクション 1 — モード

`dry_run` の現在値（true / false）を表示する。予約の書き込み系 API を抑止しているかどうかが一目で分かる。

受入条件:
- [ ] `dry_run` の値が `true` / `false`（または「抑止中」/「本番書き込み」など分かりやすい日本語）で表示される
- [ ] 出典: `config/settings.py:35`（`dry_run: bool = True`）

### 3. セクション 2 — スケジューラ時刻

APScheduler に登録される 7 ジョブの時刻と、misfire 猶予時間を表示する。

| 項目 | 値の例 | 出典 |
|------|-------|------|
| warmup（事前ログイン） | 毎日 08:55 | `config/settings.py:62,63`（`warm_up_hour` / `warm_up_minute`）+ `scheduler/runtime.py:44-48` |
| run_at_nine（定期予約実行） | 毎日 09:00 | `config/settings.py:64,65`（`run_hour` / `run_minute`）+ `scheduler/runtime.py:56-60` |
| cache_refresh（日次キャッシュ温め） | 毎日 00:05 | `scheduler/runtime.py:71`（`CronTrigger(hour=0, minute=5)`） |
| monthly_sync（座席レイアウト同期） | 毎日 03:00 | `scheduler/runtime.py:81`（`CronTrigger(hour=3, minute=0)`） |
| weekly_sync（週次スケジュール同期） | 月曜 03:30 | `scheduler/runtime.py:90`（`CronTrigger(day_of_week="mon", hour=3, minute=30)`） |
| daily_sync（予約一覧同期） | 毎日 00:00 | `scheduler/runtime.py:99`（`CronTrigger(hour=0, minute=0)`） |
| retention（履歴クリーンアップ） | 日曜 04:00 | `scheduler/runtime.py:108`（`CronTrigger(day_of_week="sun", hour=4, minute=0)`） |
| misfire 猶予 | 120 秒 | `config/settings.py:66`（`misfire_grace_time_seconds`） |

受入条件:
- [ ] 7 ジョブ全部が「曜日・時・分」のわかりやすい書式（例: `毎日 08:55`、`月曜 03:30`）で並ぶ
- [ ] misfire 猶予が秒単位で表示される
- [ ] 各行の右に出典が表示される

### 4. セクション 3 — キャッシュ・同期

| 項目 | 値 | 出典 |
|------|----|------|
| 時間割キャッシュ TTL（`_cache_ttl`） | 86400 秒（24 時間） | `app/adapters/hacomono_gateway.py:165` |
| 予約一覧同期の最小間隔（`DEFAULT_INTERVAL_SEC`） | 86400 秒（24 時間） | `app/services/sync_my_reservations.py:43` |
| 配置プレビューの先読み週数 | 4 週 | `app/services/dashboard_query.py:137,324`（`weeks=4` / `range(4)`） |

受入条件:
- [ ] 秒値の右に「（24 時間）」などの人間可読な言い換えが併記される
- [ ] 出典が正しいファイル:行で表示される

### 5. セクション 4 — 表示

| 項目 | 値 | 出典 |
|------|----|------|
| カレンダー開始時刻 | 9 時 | `config/settings.py:68`（`calendar_start_hour`） |
| カレンダー終了時刻 | 21 時 | `config/settings.py:69`（`calendar_end_hour`） |
| 履歴表示件数 | 30 件 | `app/services/dashboard_query.py:100`（`limit=30`） |

受入条件:
- [ ] 3 項目がすべて数値と出典付きで表示される

### 6. セクション 5 — 類似度・学習

| 項目 | 値 | 出典 |
|------|----|------|
| alias 類似度 ACCEPT しきい値（`_ALIAS_SIM_ACCEPT`） | 0.60 | `app/adapters/hacomono_gateway.py:35`（import）→ 定義元 |
| alias 類似度 WARN しきい値（`_ALIAS_SIM_WARN`） | 0.40 | `app/adapters/hacomono_gateway.py:38`（import）→ 定義元 |

受入条件:
- [ ] 両しきい値の値がそのまま数値（小数 2 桁）で表示される
- [ ] 「ACCEPT 以上 → 通常 upsert、WARN 以上 → 警告、WARN 未満 → スキップ」という動作が注釈として1行表示される
- [ ] 出典はしきい値の**定義元**ファイル（import 先）の行を示す

### 7. セクション 6 — 接続・タイムアウト

| 項目 | 値 | 出典 |
|------|----|------|
| reserve API タイムアウト | 15 秒 | `infra/hacomono/http.py:103`（`timeout: float = 15.0`） |
| 公開月間 API タイムアウト | 10 秒 | `infra/hacomono/public_monthly.py:53`（`timeout: float = 10.0`） |
| 連続認証失敗のロックアウト上限（`MAX_CONSECUTIVE_FAILURES`） | 3 回 | `infra/hacomono/auth.py:29` |

受入条件:
- [ ] 3 項目が単位付き（秒 / 回）で表示される

### 8. セクション 7 — 店舗

| 項目 | 値 | 出典 |
|------|----|------|
| default_studio_id | 79 | `config/settings.py:52` |
| default_studio_room_id | 177 | `config/settings.py:53` |
| default_studio_name | セントラルフィットネスクラブ府中 | `config/settings.py:54` |

受入条件:
- [ ] 3 項目（ID 2 つと店舗名 1 つ）が表示される
- [ ] ID は整数、名前は日本語文字列としてそのまま表示される

### 9. セクション 8 — 保持

| 項目 | 値 | 出典 |
|------|----|------|
| 履歴保持日数（`HISTORY_KEEP_DAYS`） | 90 日 | `scheduler/jobs/retention.py:13` |

受入条件:
- [ ] 90 日という値と出典が表示される

### 10. セクション 9 — アプリ情報

| 項目 | 値 | 出典 |
|------|----|------|
| app_name | central-sports-web | `config/settings.py:27` |
| version | 0.1.0 | `config/settings.py:28` |
| debug | false | `config/settings.py:29` |
| host | 0.0.0.0 | `config/settings.py:30` |
| port | 8080 | `config/settings.py:31` |
| timezone | Asia/Tokyo | `config/settings.py:32` |
| data_dir | BASE_DIR/data | `config/settings.py:38` |
| device_id_path | BASE_DIR/data/device_id.txt | `config/settings.py:39` + `config/settings.py:80-81`（derived） |
| scheduler_enabled | true | `config/settings.py:60` |

受入条件:
- [ ] 9 項目がすべて表示される
- [ ] `data_dir` と `device_id_path` は絶対パスとして解決済みの値を表示する

### 11. 秘匿情報は表示しない

以下の項目は **表示せず**、UI にも項目名を出さない（secret-manager の管轄）:

- `basic_auth_user` / `basic_auth_password` / `basic_auth_enabled`（`config/settings.py:43-45`）
- `discord_webhook_url`（`config/settings.py:57`）
- `secrets_dir` / `secrets_group`（`config/settings.py:48-49`）

受入条件:
- [ ] 画面ソースを grep しても上記の値がどこにも含まれていない
- [ ] 画面下部に「秘匿情報（Basic 認証 / Webhook / secrets 配置）は secret-manager で管理」という注記が1行ある

## スコープ外

- **Phase 2**: 画面からの値編集（POST での変更）。編集可否と反映タイミング（再起動要否）の整理も含む。
- **Phase 3**: hot reload（再起動なしで設定を反映する仕組み）。pydantic Settings はデフォルトで lru_cache 済みなので、どう安全に invalidate するかの設計が必要。
- 設定値の変更履歴・監査ログ。
- 複数店舗プロファイル切り替え UI。
- セクションごとのフィルタ・検索。

## 参照ドキュメント

- `tools/central-sports-web/README.md`
- `tools/central-sports-web/docs/`（もしあれば）
- Issue #1（central-sports-web 本体の傘 Issue）
- 既存のサイドバー: `tools/central-sports-web/ui/templates/partials/sidebar.html`
- 既存の read-only ビューの参考: `tools/central-sports-web/app/routes/recurring.py`
