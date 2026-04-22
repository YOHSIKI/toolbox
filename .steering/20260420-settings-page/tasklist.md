# タスクリスト

## フェーズ 0: 準備

- [x] steering ディレクトリ作成（`/workspace/toolbox/.steering/20260420-settings-page/`）※既に作成済み
- [x] 要件・設計書作成（`requirements.md` / `design.md`）※本ステアリングの生成で完了

## フェーズ 1: view service 作成

- [x] `tools/central-sports-web/app/services/settings_view.py` を新規作成
  - [x] 3 種の dataclass 定義（`SettingSource` / `SettingItem` / `SettingSection`）
  - [x] 秘匿項目（`basic_auth_*` / `discord_webhook_url` / `secrets_dir` / `secrets_group`）は参照しないことを docstring に明記
  - [x] セクションビルダ関数を 9 つ実装
    - [x] `_build_mode_section(settings)` — dry_run
    - [x] `_build_scheduler_section(settings)` — warmup / run_at_nine / cache_refresh / monthly_sync / weekly_sync / daily_sync / retention / misfire
    - [x] `_build_cache_sync_section(settings)` — `_cache_ttl` / `DEFAULT_INTERVAL_SEC` / preview 週数
    - [x] `_build_display_section(settings)` — calendar_start/end_hour / 履歴表示件数 30
    - [x] `_build_similarity_section()` — `_ALIAS_SIM_ACCEPT` / `_ALIAS_SIM_WARN`（定義元から import）
    - [x] `_build_timeout_section()` — reserve timeout / public_monthly timeout / `MAX_CONSECUTIVE_FAILURES`
    - [x] `_build_studio_section(settings)` — default_studio_id / room_id / name
    - [x] `_build_retention_section()` — `HISTORY_KEEP_DAYS`
    - [x] `_build_app_info_section(settings)` — app_name / version / debug / host / port / timezone / data_dir / device_id_path / scheduler_enabled
  - [x] 公開関数 `build_settings_view(settings: Settings) -> list[SettingSection]` を実装、9 ビルダを順に呼ぶ
  - [x] 出典メタ情報（ファイル名・行番号）はハードコードし、コード変更時は本ファイルも更新する前提である旨を docstring に書く

## フェーズ 2: ルート作成

- [x] `tools/central-sports-web/app/routes/settings.py` を新規作成
  - [x] `from fastapi import APIRouter, Depends, Request`
  - [x] `from app.deps import AppContext, get_context`
  - [x] `from app.services.settings_view import build_settings_view`
  - [x] `from app.templating import render_page`
  - [x] 既存 `recurring.py` の `Jinja2Templates` セットアップを参考にテンプレートパスを解決
  - [x] `@router.get("/reserve/settings", response_class=HTMLResponse, name="settings")` で受ける
  - [x] context と `build_settings_view` で sections を作り、`render_page(request, templates, "settings.html", {"sections": sections, "active_nav": "settings"})` で返す

## フェーズ 3: テンプレート作成

- [x] `tools/central-sports-web/ui/templates/settings.html` を新規作成
  - [x] `{% extends "base.html" %}` で既存レイアウトに載せる
  - [x] `{% block main %}` 内で `sections` をループ
    - [x] セクション見出し（`{{ section.title }}`）とセクション説明（`section.description`）
    - [x] 項目テーブル: ラベル / 値 / 補足 / 出典 の 4 列
      - [x] 出典は `<code>{{ item.source.file }}:{{ item.source.line }}</code>` で等幅・薄色・右寄せ
  - [x] 画面末尾に注記ブロック「秘匿情報（Basic 認証 / Webhook / secrets 配置）は secret-manager で管理しています」を1行追加
  - [x] 既存の `card` / `table` 系 CSS クラスが使えるなら流用、足りなければ最小限の inline style で補完

## フェーズ 4: サイドバー更新

- [x] `tools/central-sports-web/ui/templates/partials/sidebar.html` を編集
  - [x] 既存の「予約」メニューの下に `<li>` ブロックを 1 つ追加
    - [x] `class="nav-item {% if active_nav == 'settings' %}is-active{% endif %}"`
    - [x] `href="{{ url_for('settings') }}"` + 既存と同じ HTMX 属性
    - [x] 歯車アイコン（SVG インライン、既存と同じ stroke 設定）
    - [x] ラベル「設定」

## フェーズ 5: router 登録

- [x] `tools/central-sports-web/app/main.py` を編集
  - [x] `from app.routes import ... , settings, ...` の import 行に `settings` を追加
  - [x] 既存の `app.include_router(recurring.router, ...)` ブロックの直後あたりに
    ```python
    app.include_router(
        settings.router,
        dependencies=[Depends(require_basic_auth)],
        tags=["settings"],
    )
    ```
    を追加

## フェーズ 6: 静的チェック

- [x] `ruff check tools/central-sports-web/app tools/central-sports-web/ui` が警告なく通る
- [x] （任意）mypy があれば通す

## フェーズ 7: ビルド & デプロイ

- [x] `cd tools/central-sports-web && docker compose build web`
- [x] `cd tools/central-sports-web && docker compose up -d web`
- [x] `docker compose logs web --tail 50` で起動ログにエラーが出ていないか確認

## フェーズ 8: 動作確認

- [x] chromium で `http://<host>/reserve/settings` を開き、basic_auth を通す
- [x] スクリーンショット取得
- [x] 9 セクションが全部描画されていることを目視で確認
- [x] 主要な値が期待通りであることを目視で確認
  - [x] `dry_run` セクション: 現在値が表示
  - [x] `スケジューラ時刻` セクション: 7 ジョブ全部と misfire 120 秒
  - [x] `キャッシュ・同期` セクション: `_cache_ttl=86400`、`DEFAULT_INTERVAL_SEC=86400`、先読み 4 週
  - [x] `表示` セクション: カレンダー 9-21 時、履歴 30 件
  - [x] `類似度・学習` セクション: ACCEPT 0.60、WARN 0.40
  - [x] `接続・タイムアウト` セクション: reserve 15s、public_monthly 10s、連続失敗 3 回
  - [x] `店舗` セクション: studio_id 79、room_id 177、府中
  - [x] `保持` セクション: 90 日
  - [x] `アプリ情報` セクション: 9 項目
- [x] 各項目の右に出典（例: `config/settings.py:35`）が表示されている
- [x] 画面ソースを View → `Ctrl+F` で `basic_auth` / `discord_webhook` / `secrets_dir` を検索 → 1 件もヒットしないこと
- [x] サイドバー「設定」→「ダッシュボード」→「設定」と HTMX 遷移しても active_nav が正しく切り替わる
- [x] basic_auth を切った状態で `curl` → 401（任意）

---

## 振り返り

### 実装完了日
2026-04-20

### 計画と実績の差分
- `app/main.py` の `settings` モジュール名が既存の `from config.settings import Settings, get_settings` と混同しやすいため、`settings as settings_route` で別名 import にした。ruff の auto-fix によって import 文が 2 ブロックに分離されたが、機能的には同一。
- テンプレート内では既存 CSS の `card` / `card-header` / `card-body-flush` / `mono` / `muted` を流用し、4 列グリッド（ラベル / 値 / 補足 / 出典）の `.settings-row` だけをテンプレートの `{% block head_extra %}` に inline で書いた。独立した `.css` 追加は行わず、Phase 2 以降に UI 調整が出たら外出しする想定。
- 実機 `.env` で `dry_run=False` に上書きされている状態だったため、画面表示は「False（本番書き込み）」。要件例（`True`）と数値はずれるが、実行中の値を正しく反映する動作のため問題なし。

### 学んだこと
- hacomono_gateway の `_cache_ttl` はコンストラクタで代入されるインスタンス属性のため、クラス越しに参照できない。リテラル 86400 を書いて、出典でコード位置を示す実装で割り切った（設計書通り）。
- `ALIAS_SIM_ACCEPT` / `ALIAS_SIM_WARN` は `app/utils/program_similarity.py` のモジュール定数で、小数 2 桁で表示すれば十分。
- サイドバーには既存 JS（`updateActiveNav`）が URL に応じて is-active を再付与してくれるため、HTMX 遷移でも追加コード不要で active_nav が正しく切り替わる。

### 次回への改善提案
- Phase 2 で編集を入れる際は、本ファイル (settings_view.py) のハードコード出典行番号が陳腐化しやすい点に注意。import した定数（`HISTORY_KEEP_DAYS` 等）は値だけでなく **定義行番号** もテストで検証する仕組みを入れるか、出典を 1 箇所にまとめて一覧化する。
- `.env` override 値（本番で `dry_run=False`）と pydantic default（`True`）が一致しないのは想定通りだが、画面上は「default → override 後の現在値」の両方を見せた方が設定事故検知に使える可能性あり（Phase 2 で検討）。
