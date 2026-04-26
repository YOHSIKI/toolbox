# toolbox

このリポを操作する AI エージェントへの指示（ベンダー中立）。Claude 固有は `CLAUDE.md`。

ADR-0005 / 0007 の AGENTS.md 三層構造の一部。

## このリポの目的

個人ツール・スクリプト集約。日常運用で使う小さなツールを `tools/{tool-name}/` に配置する。

## 主要コマンド

| 用途 | コマンド |
|---|---|
| toolbox の compose 操作 | `GATEWAY_SOCKET_PATH=/var/run/gateway/sockets/toolbox.sock gateway docker --project <name> compose <command>` |
| 個別ツールの実行 | `tools/<tool-name>/` の README.md 参照 |

## ディレクトリ構成

```
toolbox/
├── tools/                  ディレクトリ単位のツール（README.md 必須）
├── scripts/                単発スクリプト
├── CLAUDE.md / AGENTS.md / README.md
└── .claude/                Claude Code 設定
    └── rules/              agent-config/shared/rules への symlink
```

## 共通ルール

`agent-config/shared/rules/` を symlink 経由で参照：

- `document-placement-policy.md` / `worklog-policy.md` / `decision-records-policy.md`
- `credo.md` / `container-policy.md` / `github-workflow.md` / `doc-conventions.md`
- `skill-discipline.md` / `worktree-policy.md`

## 重要な制約

- toolbox の compose 操作には `toolbox.sock` を明示する必要あり
- ツールは1ディレクトリ = 1ツール、必ず README.md を含める

## 関連

- Claude 固有: `CLAUDE.md`
- 設計判断: `agent-config/worklog/active/{slug}/decisions/`
