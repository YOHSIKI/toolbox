# toolbox


<!-- ===== ハーネス全体像（cwd 不問で即把握できるよう、4 リポ全 CLAUDE.md にコピー）===== -->
<!-- 編集は本ファイル（agent-config/templates/claude/harness-context-block.md）を更新してから -->
<!-- tools/sync-harness-context.py --apply で全リポに配布する。 -->

## ハーネス全体像

このワークスペースは **agent-config を SSOT** とするマルチリポ構成。共通資産（ルール / スキル / hooks / テンプレート / 自動化ツール）はすべて `/workspace/agent-config/` に集約され、各リポへ symlink で配布される。

- **北極星**: 介入最小・自己整備・結果信頼性
- **詳細ガイド**: [`/workspace/agent-config/CLAUDE.md`](/workspace/agent-config/CLAUDE.md) — 全ワークスペース横断の上位ガイド
- **active な作業ログ**: [`/workspace/agent-config/worklog/active/`](/workspace/agent-config/worklog/active/) — 進行中の取り組みがあればここに HANDOFF.md がある

### ハーネス設計の根幹 ADR

- ADR-0001: ハーネス全体像
- ADR-0008: worklog 全部入れ + 3 層防御（**最重要**）
- ADR-0009: hooks による絶対遵守の強制
- ADR-0011: test コンテナで secrets 必須運用
- ADR-0012: Self-healing harness（運用中の発見を記録・修復・学習）

索引: [`/workspace/agent-config/worklog/active/20260426-harness-redesign/decisions/`](/workspace/agent-config/worklog/active/20260426-harness-redesign/decisions/)

### Self-healing（運用中の気づきを記録）

「これおかしい」「これ繰り返し起きそう」と気づいたら揮発させずに記録：

```bash
python3 /workspace/agent-config/tools/finding-log.py add \
  --type {doc-bug|env-bug|code-bug|design-bug|test-gap|harness-gap} \
  --severity {low|medium|high|critical} \
  --target <path> --summary '...'
```

### 主要ツール（早見）

- `gateway rebuild` — gateway 自身のリビルド + self-restart 一発（公式 runner `gateway.rebuild`）
- `gateway audit recent / stats` — 監査ログの検索・集計
- `python3 /workspace/agent-config/tools/audit-worklog.py` — worklog の archive 監査
- `python3 /workspace/agent-config/tools/autofix.py scan` — 既知アンチパターンの検出
- `python3 /workspace/agent-config/tools/sync-harness-context.py --apply` — このブロックを全リポに再配布

<!-- ===== ここまでハーネス全体像 ===== -->

---

> **上位コンテキスト（新セッション最初に読む）**
>
> このリポは agent-config の SSOT に従う。読む順序：
>
> 1. **[`/workspace/agent-config/CLAUDE.md`](/workspace/agent-config/CLAUDE.md)** — 全ワークスペース横断の上位ガイド
> 2. 進行中: [`worklog/active/20260426-harness-redesign/HANDOFF.md`](/workspace/agent-config/worklog/active/20260426-harness-redesign/HANDOFF.md)（実装中盤）
> 3. 本ファイル（toolbox 固有運用）

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
