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

- [ ] `app/adapters/public_monthly_mapper.py`
  - [ ] `collect_closed_dates` に `kind: Literal["fixed", "special"] | None = None` 引数を追加
  - [ ] `kind="fixed"` → `datekb=1` のみ返却
  - [ ] `kind="special"` → `datekb=3` のみ返却
  - [ ] `kind=None` → 従来通り `datekb != 0` を全て返却（互換）
  - [ ] コメントを `datekb` 値の意味（0=通常 / 1=定休 / 3=特別）と合わせて更新
- [ ] `app/adapters/hacomono_gateway.py`
  - [ ] `fetch_closed_days` に `kind: str = "fixed"` パラメータを追加
  - [ ] `collect_closed_dates` 呼び出しに `kind=kind` を引き渡す
  - [ ] `_closed_days_cache` のキーに `kind` を含める
- [ ] `app/services/calendar_query.py`
  - [ ] `_fetch_closed_days` が `kind="fixed"` で gateway を呼ぶように変更
  - [ ] docstring を「店舗定休（datekb=1）のみを取得」と明記

## フェーズ 3: observed_lesson_repo に list_by_date 追加

- [ ] `db/repositories/observed_lesson_repo.py`
  - [ ] `list_by_date(db_path, *, studio_id, studio_room_id, lesson_date: date) -> list[dict]` を追加
  - [ ] 返却 dict に `start_time, program_id, program_name, instructor_id, instructor_name, studio_room_space_id, capacity, observed_at` を含める
  - [ ] 並びは `start_time ASC`
  - [ ] `__all__` に追加
- [ ] ruff check が通ること

## フェーズ 4: 仮スケジュール生成ロジック

- [ ] `app/services/calendar_query.py`
  - [ ] `_build_tentative_lessons(studio, target_dates, today) -> list[Lesson]` を新規追加
    - [ ] `target_dates` のうち `d >= today` だけ対象
    - [ ] 候補週 `[d - 7d, d - 14d, d - 21d]` を順に `observed_lesson_repo.list_by_date` で引く
    - [ ] 最初に行がヒットした週を採用し、`Lesson` オブジェクト群を生成
    - [ ] 生成 Lesson のフィールド: `studio_lesson_id=0`, `lesson_date=d`, 観測値（time/program/instructor/space/capacity）, `is_reservable=False`, `remaining_seats=None`, `state=AVAILABLE`, `release_pending=True`, `tentative_source=<候補日>`, `source_progcd=None`
    - [ ] ログ: 何日分・どの候補日から取ったかを INFO 出力（例: "tentative filled: target=2026-04-29 source=2026-04-22 lessons=5"）
  - [ ] `build_week` 内の処理に組み込む
    - [ ] reserve API 経路: public_monthly fill を終えた後に再度 `covered` を計算し、残った missing を `_build_tentative_lessons` に渡す
    - [ ] out_of_range 経路: public_monthly 取得後に同様の補完を追加
    - [ ] 生成した tentative に `_annotate_recurring_state` を適用
    - [ ] lessons に extend
  - [ ] `_fetch_closed_days` の返却（`closed_days`）は `fixed_closed_days`（datekb=1 のみ）として使う。missing 計算からは `fixed_closed_days` を除外する
  - [ ] `CalendarWeek.closed_dates` には `fixed_closed_days` を入れる（UI は表示しないが、将来参照のために保持）

## フェーズ 5: UI テンプレート更新

- [ ] `ui/templates/reserve_calendar.html`
  - [ ] 日付ヘッダから `cal-head--closed` クラス付与と「休館」バッジ span を削除
  - [ ] lesson セルの class 合成に `{% if lesson.tentative_source %}lesson--tentative{% endif %}` を追加
  - [ ] lesson セル内に `{% if lesson.tentative_source %}<span class="lesson-badge-tentative">仮</span>{% endif %}` を表示
  - [ ] 凡例（`.legend`）に `legend-swatch--tentative` + ラベル「仮スケジュール」を追加
  - [ ] 右パネル Intent 登録フォームの説明文を微調整（仮スケジュールの場合に「過去データを元に予測したレッスンです」と補足）

- [ ] CSS（`ui/static/styles.css` またはテンプレート直近の該当箇所）
  - [ ] `.lesson--tentative` — 背景色を薄いグレー（既存のトークンを流用できれば流用、なければ `--muted` 系）
  - [ ] `.lesson-badge-tentative` — 小さいピル状バッジ（「仮」文字）
  - [ ] `.legend-swatch--tentative` — 凡例用 swatch

## フェーズ 6: ユニットテスト

- [ ] `tests/` 配下に仮スケジュール生成の単体テストを追加（既存のテスト配置慣習に従う）
  - [ ] 1 週前に観測あり → 1 週前データで仮 lesson が生成される
  - [ ] 1 週前ゼロ件・2 週前ありで 2 週前のデータが採用される
  - [ ] 3 週前まで全て無しなら空配列
  - [ ] 過去日（today 未満）は常に空配列
- [ ] `pytest` 実行で新規テストがパスする

## フェーズ 7: 静的チェック

- [ ] `ruff check app/ db/ ui/` — 新規違反なし（既存違反は触らない）
- [ ] 旧 `collect_closed_dates` / `fetch_closed_days` の引数デフォルト挙動が変わっていないこと（他呼び出し元を grep で確認）
- [ ] `grep -r "tentative_source" tools/central-sports-web/` で想定箇所のみ変更されていることを確認

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

## フェーズ 10: 振り返り

- [ ] 計画と実績の差分を振り返り欄に記録
- [ ] 学んだこと（observed_lessons の件数が乏しいケースの挙動、UI の薄色トーンの調整など）を記録
- [ ] 次回への改善提案（例: `datekb=2` 観測時の扱い、仮スケジュールの TTL キャッシュ導入など）

---

## 振り返り

### 実装完了日
YYYY-MM-DD

### 計画と実績の差分
- {計画と異なった点とその理由}

### 学んだこと
- {技術的な知見やプロセス改善点}

### 次回への改善提案
- {次回の改善点}
