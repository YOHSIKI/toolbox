# central-sports-web

セントラルスポーツの会員サイト（hacomono 上で稼働）に対して、朝 9:00 の予約開放をメモリ常駐のスケジューラが自動で取りにいく Web アプリ。ダッシュボードで結果を見られて、カレンダーから単発予約も操作できる。

仕様は `docs/design/spec.md`、アーキテクチャは `docs/design/architecture.md`、用語は `docs/design/glossary.md` を参照。

## 特長

- **毎朝 9:00 の自動予約**: ログイン済みセッションを常駐させ、予約開放の瞬間に POST を飛ばす。立ち上げオーバーヘッドがない
- **Outlook 風の 1 カレンダー**: 定期予約で取れた予約も、単発カレンダーでそのまま「予約済み」として表示
- **プログラム ID ベースの識別**: 代行や表記揺れでレッスンを取り逃さない
- **Bot 偽装**: Chrome 131 の TLS/HTTP2 指紋を偽装し、Chrome 標準の XHR ヘッダを全リクエストに付与
- **dry-run 既定**: 書き込み系（reserve/cancel/move）のみ抑止。読み取り系は常に本物を叩く
- **シークレット境界**: dev-admin では復号しない。常駐コンテナに読み取り専用でマウントされた Fernet 暗号化ファイルを起動時に 1 度だけ復号してメモリに保持

## ディレクトリ構成

```
tools/central-sports-web/
├── app/
│   ├── domain/        # ドメインモデル・ポート（Protocol）・ドメイン例外
│   ├── services/      # ユースケース層（calendar_query, reserve_single, reserve_recurring, ...）
│   ├── adapters/      # ReservationGateway の実装、生レスポンス→ドメインへの変換
│   ├── routes/        # FastAPI のルート
│   ├── auth.py        # Basic 認証
│   ├── deps.py        # DI（AppContext の組み立て）
│   ├── lifespan.py    # 起動・停止フック
│   └── main.py
├── infra/
│   ├── hacomono/      # Bot 偽装 HTTP クライアント、認証、エラー階層、マスキング
│   ├── secrets/       # Fernet 復号（SecretsBundle）
│   └── notifier/      # Discord Webhook
├── db/
│   ├── schema.py
│   ├── connection.py
│   ├── migrations.py
│   └── repositories/  # studio / recurring / reservation / history / schedule_cache
├── scheduler/
│   ├── runtime.py     # APScheduler
│   └── jobs/          # warmup / run_at_nine / cache_refresh / daily_sync / retention
├── ui/
│   ├── templates/     # Jinja2 (base/sidebar/topbar/dashboard/reserve_calendar/reserve_recurring)
│   └── static/        # styles.css（モック準拠）
├── config/settings.py
├── scripts/           # verify_signin.py / verify_schedule.py / verify_reservations.py
├── tests/             # infra / adapters / db のユニットテスト
├── mockup/            # 静的モック（参考、本体からは参照しない）
├── docs/design/       # 永続仕様書
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## 開発用セットアップ（dev-admin で画面だけ見る）

secrets にアクセスできない dev-admin 環境では、UI は「未認証」バッジ付きで起動し、
カレンダー・配置プレビューなど実 API を叩く画面は「認証情報が未設定のため」エラーになる。
これは意図した挙動（dev-admin でログインさせない）。

```bash
cd tools/central-sports-web
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
CSW_BASIC_AUTH_ENABLED=false .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```

`http://localhost:8080/` へアクセス。

## 本番（常駐コンテナ）

### ビルドと起動

```bash
cd /volume1/toolbox/tools/central-sports-web  # NAS 上のパス
docker compose up -d --build
```

dev-admin からは `gateway docker --project toolbox compose up -d` で同等操作を実行する。

### 環境変数の主要項目

| 変数 | 既定 | 用途 |
|---|---|---|
| `CSW_DRY_RUN` | `true` | 予約 POST/キャンセル/席変更を抑止 |
| `CSW_BASIC_AUTH_USER` | `admin` | Web UI 認証ユーザー名 |
| `CSW_BASIC_AUTH_PASSWORD` | `changeme` | Web UI 認証パスワード（本番では必ず上書き） |
| `CSW_SECRETS_DIR` | `/workspace/.secrets` | Fernet マスター鍵と暗号化ファイルの置き場所 |
| `CSW_SECRETS_GROUP` | `central-sports` | 復号対象のグループ名 |
| `CSW_DEFAULT_STUDIO_ID` | `79` | 府中（初期シード） |
| `CSW_DEFAULT_STUDIO_ROOM_ID` | `177` | 同上 |
| `CSW_DISCORD_WEBHOOK_URL` | 未設定 | 空なら通知抑止 |

### Secrets の準備

`/volume1/infra/secrets/.secrets.yaml.enc` に次のグループを登録する（既存の事前調査と同じ）:

```yaml
central-sports:
  email: "your@email.example"
  password: "your-password"
```

docker-compose.yml は `/volume1/infra/secrets` を `/workspace/.secrets:ro` としてマウントする。

### 動作確認

常駐コンテナ内で疎通確認スクリプトを実行する（メール・パスワード・トークンは出力されない）:

```bash
docker exec -it central-sports-web python scripts/verify_signin.py
docker exec -it central-sports-web python scripts/verify_schedule.py --days-ahead 7
docker exec -it central-sports-web python scripts/verify_reservations.py
```

出力 `ok: true` かつ `lesson_count > 0` なら、スケジュール取得まで通っている。

### 本番 POST を有効化する

dry-run を外すには `.env` に `CSW_DRY_RUN=false` を設定して再起動:

```bash
docker compose up -d
```

最初の 1 回は、取り消しやすい安価なレッスンで「予約する → すぐ取消」のテストを推奨。

## 画面構成

| URL | 内容 |
|---|---|
| `GET /` | ダッシュボード（当日サマリー・現在の予約・予約予定・予約履歴） |
| `GET /reserve` | 単発予約（カレンダー＋座席パネル） |
| `GET /reserve/recurring` | 定期予約一覧＋配置プレビュー |
| `GET /reserve/recurring/new` | 定期予約の追加フォーム |
| `POST /studios/switch` | 店舗切替（Cookie） |
| `GET /healthz` | 外形監視用（Basic 認証なし） |
| `GET /meta` | 稼働設定の確認 |

## 事前調査スクリプト（参考）

`/workspace/toolbox/tools/central-sports/` 配下に `cs_api.py` ほか、API 仕様確認用のスクリプトがある。
これは本体から import してはおらず、仕様が変わった時に手元で挙動を確認するための資材として残している。

## テストと静的解析

```bash
.venv/bin/pytest -q
.venv/bin/ruff check .
```
