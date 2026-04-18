# central-sports — セントラルスポーツ予約自動化ツール

`reserve.central.co.jp`（Nuxt SPA + hacomono Rails API）向けの自動予約スクリプト群。

## 前提

- 実行環境: **toolbox-exec コンテナ**（dev-admin では動かない）。secrets が必要なため Claude 在席の dev-admin からは実行不可。
- secrets: `central-sports.mail_address`（または `email`）と `central-sports.password` が `/volume1/infra/secrets/.secrets.yaml.enc` に登録済みであること。
- 依存: `/workspace/toolbox/.venv/` に requests / pyyaml / cryptography インストール済み。

## 使い方（gateway 経由）

1. スクリプトを 3LLM 審査に通してハッシュ登録:
   ```bash
   gateway --project toolbox review tools/central-sports/fetch_schedule.py
   ```
2. 審査通過後に実行:
   ```bash
   gateway --project toolbox exec tools/central-sports/fetch_schedule.py \
     --studio 79 --room 177 --date 2026-04-25
   ```
   `--target exec` が既定。test コンテナで動かす場合は `--target test`。

## 店舗/ルームの識別

| studio_id | studio_room_id | 店舗 |
|---|---|---|
| 79 | 177 | セントラルフィットネスクラブ府中 |

## API 仕様（調査結果）

- ベース URL: `https://reserve.central.co.jp/api`
- 認証: `POST /system/auth/signin` with `{mail_address, password}` + `Cookie: device_id=<40hex>` + `X-Requested-With: XMLHttpRequest`
  - 成功時: `Set-Cookie: _at=<access_token>`
- スケジュール: `GET /master/studio-lessons/schedule?query=<URL-encoded JSON>`
  - query: `{schedule_type: "rooms_fill", studio_id, studio_room_id, date_from, date_to}`
  - 未ログイン時は `schedule: null`、ログイン後に枠リストが入る
- 予約: `POST /reservation/reservations/reserve`（本スクリプトでは未実装。スケジュール取得後の次段）

## ファイル構成

- `secrets.py` — Fernet 復号で `central-sports` group を取得するだけの最小クラス
- `cs_api.py` — `Session.signin / get_schedule / signout` を持つ HTTP クライアント
- `fetch_schedule.py` — CLI: ログイン → スケジュール取得 → マスク済み要約を stdout 出力
- `README.md`（これ）

## 設計ルール（重要）

- **復号した secret 値は script 内だけで扱う。**stdout / stderr / ファイルに平文を書かない。
- **レスポンスに echo back された値もマスクしてから出力**する（`_mask()` を使う）。
- 値を検証したい場合は長さ比較や存在チェックに留め、値そのものや先頭 N 文字も出さない。

## 今後の拡張予定

- `reserve.py` — 指定レッスンへの予約 POST（先着レース対応、9:00 ちょうどの発火）
- `watch.py` — 空き監視（cron 定期実行、空き出たら Discord 通知）
