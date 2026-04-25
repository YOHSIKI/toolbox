# toolbox

日常ツール・アイデア管理用リポジトリ。

## 構成

- `tools/` — ツール（ディレクトリ単位で管理）
- `scripts/` — 単発スクリプト

## ルール

- ツールは `tools/<tool-name>/` にディレクトリを切って配置する
- 各ツールには README.md を含める
- 単発のスクリプトは `scripts/` に配置する

## デプロイ・コンテナ操作

`tools/<name>/docker-compose.yml` を持つツール（例: `central-sports-web`）の build / restart は **必ず `GATEWAY_SOCKET_PATH=/var/run/gateway/sockets/toolbox.sock` を明示** する。

```bash
GATEWAY_SOCKET_PATH=/var/run/gateway/sockets/toolbox.sock \
  gateway docker --project central-sports-web compose up -d --build web
```

dev-admin のデフォルト socket は market-platform 用なので、toolbox の compose dirs (`run`, `central-sports-web`, …) は見えない。`Error (400): invalid project: ...` が返ったら socket を疑う。詳細は `agent-config/shared/rules/container-policy.md` の「gateway socket と project context」を参照。
