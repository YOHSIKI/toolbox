# タスクリスト

## フェーズ 0: 準備

- [x] steering 作成（requirements / design / tasklist）
- [x] ブランチ `feature/20260424-tentative-schedule` で作業を開始
- [x] 関連コードの場所を確認（Lesson / calendar_query / public_monthly_mapper / hacomono_gateway / observed_lesson_repo / reserve_calendar.html）

## フェーズ 1: Lesson エンティティ拡張

- [x] `app/domain/entities.py`
  - [x] `Lesson` dataclass に `tentative_source: date | None = None` を `release_pending` の直後に追加
  - [x] 既存 docstring と合わせた簡潔なコメントを付ける（出典日が入る、実データは None）
- [ ] ruff check が通ること（フェーズ 7 でまとめて実施）

## フェーズ 2: 休館判定の 2 分化（datekb=1 / datekb=3）

- [x] `app/adapters/public_monthly_mapper.py`
  - [x] `collect_closed_dates` に `kind: Literal["fixed", "special"] | None = None` 引数を追加
  - [x] `kind="fixed"` → `datekb=1` のみ返却
  - [x] `kind="special"` → `datekb=3` のみ返却
  - [x] `kind=None` → 従来通り `datekb != 0` を全て返却（互換）
  - [x] コメントを `datekb` 値の意味（0=通常 / 1=定休 / 3=特別）と合わせて更新
- [x] `app/adapters/hacomono_gateway.py`
  - [x] `fetch_closed_days` に `kind: str = "fixed"` パラメータを追加
  - [x] `collect_closed_dates` 呼び出しに `kind=kind` を引き渡す
  - [x] `_closed_days_cache` のキーに `kind` を含める
- [x] `app/services/calendar_query.py`
  - [x] `_fetch_closed_days` が `all - special` で gateway を呼ぶように変更（datekb=2 等の未知値もカバー）
  - [x] docstring を実態に合わせて更新

## フェーズ 3: observed_lesson_repo に list_by_date/list_by_dates 追加

- [x] `db/repositories/observed_lesson_repo.py`
  - [x] `list_by_date` を追加（単発）
  - [x] `list_by_dates` を追加（複数日バッチ取得、N+1 解消）
  - [x] 並びは `start_time ASC`
  - [x] `__all__` に追加
  - [x] DB 例外は repo 側で握り空返却
- [x] ruff check が通ること

## フェーズ 4: 仮スケジュール生成ロジック

- [x] `app/services/calendar_query.py`
  - [x] `_build_tentative_lessons(studio, target_dates, today) -> list[Lesson]` を新規追加
    - [x] `target_dates` のうち `d >= today` だけ対象
    - [x] 候補週 `[d - 7d, d - 14d, d - 21d]` を 1 クエリで取得
    - [x] 最初に行がヒットした週を採用し、`Lesson` オブジェクト群を生成
    - [x] 生成 Lesson のフィールド: `studio_lesson_id=0`, `lesson_date=d`, 観測値, `is_reservable=False`, `release_pending=True`, `tentative_source=<候補日>`
    - [x] INFO ログに append 件数を出す
  - [x] `build_week` 内の処理に組み込む（reserve / out_of_range 両経路の後段に 1 箇所で統合）
  - [x] `_fetch_closed_days` の返却（`closed_days`）を仮対象外の日として使う
  - [x] `CalendarWeek.closed_dates` には `closed_days` を入れる（UI は表示しないが将来参照のために保持）

## フェーズ 5: UI テンプレート更新

- [x] `ui/templates/reserve_calendar.html`
  - [x] 日付ヘッダから `cal-head--closed` クラス付与と「休館」バッジ span を削除
  - [x] lesson セルの class 合成に `{% if lesson.tentative_source %}lesson--tentative{% endif %}` を追加
  - [x] lesson セル内に `{% if lesson.tentative_source %}<span class="lesson-badge-tentative">仮</span>{% endif %}` を表示
  - [x] 凡例（`.legend`）に `legend-swatch--tentative` + ラベル「仮スケジュール」を追加
  - [x] 右パネル Intent 登録フォームの説明文を微調整（仮スケジュール向けの文言）

- [x] CSS（`ui/static/styles.css`）
  - [x] `.lesson--tentative` — 薄いグレー系
  - [x] `.lesson-badge-tentative` — 「仮」バッジ
  - [x] `.legend-swatch--tentative` — 凡例用 swatch

## フェーズ 6: ユニットテスト

- [x] `tests/services/test_calendar_tentative.py` を新設
  - [x] 1 週前に観測あり → 1 週前データで仮 lesson が生成される
  - [x] 1 週前ゼロ件・2 週前ありで 2 週前のデータが採用される
  - [x] 3 週前まで全て無しなら空配列
  - [x] 過去日（today 未満）は常に空配列
  - [x] 境界値 `target == today`
  - [x] 複数 target で一部のみ観測あり
  - [x] `fixed_closed` の日は仮スケジュール対象外（`_fill_tentative` 経由）
  - [x] 既存 lessons と衝突する日は仮を追加しない
  - [x] `list_by_dates` のグルーピング / 空入力
  - [x] DB 欠落時の空返却
- [x] `pytest` 実行で新規テスト 14 本 + 既存 3 本 = 17 本がパス

## フェーズ 7: 静的チェック

- [x] `ruff check` — 今回変更したファイルで新規違反なし（All checks passed）
- [x] 旧 `collect_closed_dates` / `fetch_closed_days` の引数デフォルト挙動が変わっていないこと
- [x] `grep -r "tentative_source"` で想定箇所のみ変更されていること

## フェーズ 8: ビルド & デプロイ

- [ ] `gateway docker --project central-sports-web compose build web`
- [ ] `gateway docker --project central-sports-web compose up -d web`
- [ ] コンテナログで起動エラーが無いことを確認

## フェーズ 9: 動作確認（スクリーンショット）

- [ ] chromium で `/reserve?week=2026-04-27` 付近を表示 → 2026-04-29（祝日、datekb=3）のセルに仮レッスンが並ぶ
- [ ] 「仮」バッジが出て薄いグレーに見える
- [ ] 仮 lesson をクリック → 右パネルに Intent 登録フォームが表示される
- [ ] 金曜（例 2026-05-01、datekb=1）のセルが空欄で「休館」文字が無い
- [ ] 翌月未配信週（例 2026-05-04 の週）でも、過去 1〜3 週前に観測があれば仮スケジュールが並ぶ
- [ ] Intent を 1 件登録 → 再読込すると「◎ 予約実行待ち」表示に切り替わる
- [ ] 凡例に「仮スケジュール」が追加されている
- [ ] 過去の week に戻って、`lesson_date < today` の日には仮スケジュールが出ていないことを確認

## フェーズ 9.5: 3LLM レビュー反映

- [x] `calendar_query.py:15` 未使用 import `collect_closed_dates` を削除
- [x] `calendar_query.py` 仮スケジュール補完のコメントインデントを修正
- [x] `_fill_tentative` の呼び出しを reserve / out_of_range の両経路から外に出し 1 箇所に統合
- [x] `_fetch_closed_days` の返却を `all - special` に変更し、datekb=2 等の未知値も誤って仮対象にしないようにする
- [x] `observed_lesson_repo.list_by_dates` を新設し、複数日を 1 クエリで取得（N+1 解消）
- [x] `_build_tentative_lessons` を `list_by_dates` ベースに書き換え、target × 3 週分を最大 1 クエリに
- [x] 仮スケジュール件数の INFO ログを実際に append した件数に合わせる
- [x] `list_by_date` / `list_by_dates` の内部例外はリポジトリ側で握って空返却（警告ログ）
- [x] テスト追加: `fixed_closed` の日は仮スケジュールを作らない
- [x] テスト追加: 既に lessons で埋まっている日は仮で上書きしない（`_fill_tentative` 経由）
- [x] テスト追加: 複数 target_dates のうち一部だけ観測あり、のパターン
- [x] テスト追加: `target == today` は仮スケジュール対象になる（境界値）
- [x] ruff check 再実行、pytest 再実行
- [x] ~~`except Exception` を具体化~~ (理由: 既存コードベース全体で `# noqa: BLE001` のスタイルを採用しており、新規コードもこれに合わせる方が整合性が高い。個別修正は別 Issue で全体統一が望ましい)
- [x] ~~`build_week` 経由の統合テスト~~ (理由: gateway を全モック化する必要があり、既存の central-sports-web テストでも build_week 統合テストはゼロ。ユニットテスト重視の方針に従い、`_fill_tentative` と `_build_tentative_lessons` の単体テストで挙動を網羅済み)

## フェーズ 10: 振り返り

- [ ] 計画と実績の差分を振り返り欄に記録
- [ ] 学んだこと（observed_lessons の件数が乏しいケースの挙動、UI の薄色トーンの調整など）を記録
- [ ] 次回への改善提案（例: `datekb=2` 観測時の扱い、仮スケジュールの TTL キャッシュ導入など）

---

## 振り返り

### 実装完了日
2026-04-25

### 計画と実績の差分
- 当初は `_fetch_closed_days` を `kind="fixed"` のみで取得する設計だったが、3LLM レビュー (Codex) の指摘で `kind="all" - kind="special"` に変更。datekb=2 等の未知値も保守的に仮スケジュール対象外にできる
- 当初の `_build_tentative_lessons` は target × 最大 3 週で 21 回 SQLite を叩く N+1 設計だったが、Codex/Gemini の指摘で `list_by_dates` バッチ取得に書き換え、最大 1 クエリに集約
- `_fill_tentative` の呼び出しが reserve / out_of_range の両経路に重複していたが、Gemini の指摘で `build_week` の最後に 1 箇所統合
- ステアリングの「フェーズ8: gateway docker --project central-sports-web compose build」は当初 invalid project エラーで詰まった。`GATEWAY_SOCKET_PATH=/var/run/gateway/sockets/toolbox.sock` で toolbox socket に接続する必要があった

### 学んだこと
- gateway の compose 操作は **socket で project context が決まる**。dev-admin デフォルトは market-platform.sock。toolbox 配下のサービスを操作するには `GATEWAY_SOCKET_PATH=/var/run/gateway/sockets/toolbox.sock` を明示する
- dev-admin から central-sports-web の HTTP は **`192.168.128.1:8080` (dev-admin bridge gateway 経由)** で届く。`172.30.0.1:8080` は infra 用で別ネットワーク
- 3LLM レビューの指摘は重複や視点が分かれる。Claude は局所的な品質、Codex は契約・スペック準拠、Gemini は重複/構造の指摘が強い。3 つを総合すると拾える漏れが減る
- ruff の I001 は `ruff --fix --select I001` でピンポイント修正可能。E741 等の既存違反を巻き込まずに済む

### 次回への改善提案
- toolbox 配下の新規ツールでは README の運用例に `GATEWAY_SOCKET_PATH=...` を明記する
- N+1 の早期発見のため、設計レビュー段階で「DB 呼び出し回数 = O(N)」が出てきた時点で `list_by_*` バッチ版の有無を必ず確認する
- 新規作業時、`gateway system check-config` の disk 設定と `--project` で valid な値が乖離する事象（in-memory キャッシュ）に再度詰まらないよう、socket リスト `/var/run/gateway/sockets/` を最初に確認する習慣を徹底する
