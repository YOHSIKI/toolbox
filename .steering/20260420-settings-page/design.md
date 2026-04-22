# 設計書

## アーキテクチャ概要

既存の FastAPI + Jinja2 + HTMX 構成にそのまま乗せる。Phase 1 は**読むだけ**なので新規依存は無し。routes / services / templates の 3 層で、サービス層に「設定カタログを view 用 dataclass に整形する関数」を置く。ルート層はそれを呼んでテンプレートに渡すだけ。

```
Browser ──GET /reserve/settings──▶ app.routes.settings
                                        │
                                        ▼
                              app.services.settings_view
                                        │   （各モジュールから値を集め、出典メタ情報を付けて
                                        │    SettingsSection のリストを組み立てる）
                                        ▼
                              ui/templates/settings.html
                                        │   （base.html を extend し、
                                        │    セクション単位でテーブル描画）
                                        ▼
                                   HTML レスポンス
```

HTMX 部分更新は既存の `hx-get ... hx-target="#hx-main"` パターンに従い、サイドバーから遷移したときも全画面再描画せず本文だけ差し替わるようにする。

## コンポーネント設計

### 1. `app/services/settings_view.py`（新規）

**責務**:
- 実行時の設定値を各モジュールから集めて、view 用 dataclass に整形して返す
- 出典メタ情報（ファイル名と行番号）を**コード上にハードコード**で保持する（行番号は自動追跡しない。コード変更時は本ファイルも併せて更新する前提）
- 秘匿情報（basic_auth / discord_webhook / secrets_dir / secrets_group）は **取得も参照もしない**

**実装の要点**:
- 3 種類の dataclass で構造化する:

  ```python
  @dataclass(frozen=True)
  class SettingSource:
      file: str   # 例: "config/settings.py"
      line: int   # 例: 35

  @dataclass(frozen=True)
  class SettingItem:
      label: str            # 「dry_run」「時間割キャッシュ TTL」
      value_display: str    # 「True（抑止中）」「86400 秒（24 時間）」
      note: str | None      # 補足説明（任意、1 行）
      source: SettingSource

  @dataclass(frozen=True)
  class SettingSection:
      title: str              # 「モード」「スケジューラ時刻」
      description: str | None # セクションの 1 行説明
      items: list[SettingItem]
  ```

- エントリポイント関数 `build_settings_view(settings: Settings) -> list[SettingSection]` を公開する
- 各セクションのビルダを小関数に分ける（`_build_mode_section`, `_build_scheduler_section`, ...）
- `_cache_ttl` などインスタンス属性の値は、`get_settings()` 由来の Settings から直接取らない値については**コード上の定数を import して参照する**。具体的には:
  - `from scheduler.jobs.retention import HISTORY_KEEP_DAYS`
  - `from infra.hacomono.auth import MAX_CONSECUTIVE_FAILURES`
  - `app/adapters/hacomono_gateway.py` の `_cache_ttl` は**インスタンス属性**なのでクラスからは取りづらい。リテラル `86400` を直接書く（出典メタ情報で「ここを見れば書いてある」と分かれば十分）
  - `ALIAS_SIM_ACCEPT` / `ALIAS_SIM_WARN` は定義元モジュールから import して実値を使う
  - `DEFAULT_INTERVAL_SEC` はクラス属性なので `SyncMyReservationsService.DEFAULT_INTERVAL_SEC` で参照
- `infra/hacomono/http.py` / `public_monthly.py` のタイムアウトはコンストラクタのデフォルト引数値。実行時にいま使われている値と一致するとは限らないため、**表示値はデフォルト値のリテラル**を使い、出典をコンストラクタ定義行に向ける

### 2. `app/routes/settings.py`（新規）

**責務**:
- `GET /reserve/settings` を受けて `settings_view.build_settings_view()` を呼び、テンプレートをレンダリングする

**実装の要点**:
- `router = APIRouter()` を公開
- 既存ルート（`recurring.py` 等）と同じ `render_page` / `Jinja2Templates` パターンを踏襲
- 依存: `context: AppContext = Depends(get_context)` で `context.settings` を取得
- 実装は 20 行程度。エンドポイント名は `name="settings"` で `url_for('settings')` から参照できるようにする

### 3. `ui/templates/settings.html`（新規）

**責務**:
- `base.html` を extend し、`active_nav = 'settings'` をセットする
- セクションのリストをループして、各セクション見出し・説明・項目テーブルを描画する
- 各項目は「ラベル | 値 | 補足 | 出典（ファイル:行 を小さく表示）」の 4 列構造

**実装の要点**:
- 既存 CSS の `nav` / `card` / `table` クラスを流用（新規 CSS 追加は最小限）
- 出典の表示は `<code class="src">config/settings.py:35</code>` のように、等幅フォント＋薄色で右寄せ
- 画面末尾に「秘匿情報は secret-manager で管理」の注記を1行
- HTMX で部分差し替えされる前提なので、`{% block main %}...{% endblock %}` の中に閉じる

### 4. `ui/templates/partials/sidebar.html`（既存、1項目追加）

**責務**:
- 既存の「ダッシュボード」「予約」の下に「設定」メニューを追加する

**実装の要点**:
- 既存 2 項目と同じ構造（`<li>` + `<a class="nav-item">` + 歯車 SVG + ラベル）
- `{% if active_nav == 'settings' %}is-active{% endif %}` を付ける
- `href="{{ url_for('settings') }}"` と HTMX 属性

### 5. `app/main.py`（既存、router 登録のみ）

**責務**:
- 新しい `settings` router を `include_router` で登録する（basic_auth 依存つき）

**実装の要点**:
- `from app.routes import dashboard, debug, ..., settings, ...` に `settings` を追加
- 既存の `recurring` と同じブロック構造で 4 行追加するだけ

## データフロー

```
1. Browser → GET /reserve/settings
2. FastAPI が basic_auth チェック → 通ったら settings_route に入る
3. settings_route: context.settings と各種定数を集約
4. build_settings_view(settings) が list[SettingSection] を返す
5. Jinja2 が settings.html をレンダリング（9 セクション、30 項目弱）
6. Browser が HTML を受け取り描画。HTMX 経由なら #hx-main だけ差し替え
```

副作用なし・DB アクセスなし・外部 API 呼び出しなし。ページ表示は純粋関数的に完結する。

## エラーハンドリング

Phase 1 は read-only で、失敗する可能性のある操作は無い。想定する異常パスと対応:

- **import エラー** （`HISTORY_KEEP_DAYS` 等のシンボル名が将来変わった場合）: アプリ起動時点で発覚する。ランタイムでは起きない。
- **テンプレート存在しない** / **`url_for('settings')` 解決できない**: 500 エラー。FastAPI 標準のハンドラに任せる。
- basic_auth 不通過は 401（既存の `require_basic_auth` 依存で処理済み）。

専用の try/except は置かない。

## テスト方針

- **ユニットテスト**: Phase 1 では pytest を追加しない。build_settings_view の出力が妥当かは目視で確認する。将来 Phase 2 で編集機能を入れるときに dataclass のテストを足す。
- **統合テスト**: なし。
- **動作確認**: test コンテナではなく実際のコンテナで `docker compose up -d web` → chromium でスクリーンショット取得 → 9 セクション全ての項目が正しい値と出典で表示されているか目視チェック。
  - チェック項目:
    - サイドバーから「設定」をクリックして遷移できる
    - 9 セクション見出しと合計 30 項目弱がすべて描画される
    - 値が期待通り（`dry_run=true`、`warmup=08:55`、`_cache_ttl=86400`、`_ALIAS_SIM_ACCEPT=0.60` 等）
    - 出典が `config/settings.py:35` のような形で右寄せ表示される
    - 画面ソースを表示 → grep で `basic_auth` / `discord_webhook` / `secrets_dir` がヒットしないこと
    - basic_auth を切って未認証アクセス → 401 になること（任意）
    - HTMX 部分差し替えでも正しく active_nav が更新されること

## ファイル構成

```
tools/central-sports-web/
├── app/
│   ├── main.py                         # [変更] settings router を追加登録
│   ├── routes/
│   │   └── settings.py                 # [新規] GET /reserve/settings
│   └── services/
│       └── settings_view.py            # [新規] dataclass 定義 + build_settings_view
└── ui/
    └── templates/
        ├── settings.html               # [新規] セクション表示
        └── partials/
            └── sidebar.html            # [変更] 「設定」メニュー追加
```

新規 3 ファイル、変更 2 ファイル。合計 5 ファイル。

## 実装の順序

1. `app/services/settings_view.py` を書く（dataclass 3 種 + 9 セクションのビルダ関数）
2. `app/routes/settings.py` を書く（20 行の薄いルート）
3. `ui/templates/settings.html` を書く（`base.html` extend、セクションループ）
4. `ui/templates/partials/sidebar.html` に「設定」メニュー1項目を追加
5. `app/main.py` に `settings` router を `include_router` 登録
6. ruff check（ローカル）
7. ビルド + デプロイ（`compose build web` → `compose up -d web`）
8. chromium で動作確認（スクリーンショット取得、全項目目視チェック）

各ステップは小さく、途中で問題が出ても 1 つ戻すだけで直る。
