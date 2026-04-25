# 設計書

## アーキテクチャ概要

**考え方**: 公開月間 API から lesson を取れない日について、`observed_lessons` テーブル（reserve API で過去に観測したレッスン）を使って「過去の同曜日のスナップショット」を組み立て、仮 lesson として `build_week` の出力に混ぜる。Intent 登録は既存の `release_pending` 経路を再利用する（Lesson に `tentative_source` というマーカーを追加して UI 表示だけ差し替える）。

休館判定は `datekb=1`（店舗定休）と `datekb=3`（祝日特別日）に分離する。`datekb=1` は従来通り「確定休館」として仮スケジュール対象外、`datekb=3` は補完対象として扱う。

```
build_week(studio, week_start, today):
  open_until = today + 6
  out_of_range = (week_start > open_until)
  lessons = []
  fixed_closed_days = fetch_closed_days(kind="fixed")   # datekb=1 のみ
  if not out_of_range:
    lessons = reserve_api.fetch_week(...)
    annotate_reserved_state(lessons, today)
    annotate_recurring_state(lessons, today)
    missing = week_dates - covered - fixed_closed
    fill_lessons = fetch_public_monthly_week(missing)    # datekb=3 は含まれない
    lessons.extend(fill_lessons, with release_pending)
  else:
    lessons = fetch_public_monthly_week(week_start)
    annotate_recurring_state(lessons, today)

  # 新規: 仮スケジュール補完
  covered = {l.lesson_date for l in lessons}
  week_dates = {week_start + i for i in 0..6}
  tentative_targets = week_dates - covered - fixed_closed   # today 以降に限る
  tentative = build_tentative_lessons(studio, tentative_targets, today)
  lessons.extend(tentative, with release_pending=True, tentative_source=<出典日>)

  annotate_intent_state(lessons)
  return CalendarWeek(..., closed_dates=fixed_closed_days)   # UI が「休館」出さないため空配列扱いでもOK
```

## コンポーネント設計

### 1. `Lesson` エンティティ拡張

**責務**:

- 仮スケジュール由来の lesson を内部的にマークする

**実装の要点**:

- `tentative_source: date | None = None` を追加（デフォルトは `None`、仮データなら出典日）
- `release_pending=True` と `tentative_source=<date>` は独立。両方立つのが仮スケジュール、`release_pending=True` のみが 9:00 開放待ちの最新日
- 既存コードで `Lesson(...)` をキーワード引数で生成している箇所は影響しない

### 2. `public_monthly_mapper.collect_closed_dates` の 2 分化

**責務**:

- `datekb=1`（店舗定休）と `datekb=3`（祝日特別日等）を区別して返す

**実装の要点**:

- 既存の `collect_closed_dates` は「0 以外は全て closed」として返していた。これを分割する
- 新 API: `collect_closed_dates(payload, *, year, month, kind: Literal["fixed", "special"] | None = None) -> set[date]`
  - `kind="fixed"` → `datekb=1` のみ返す（UI の「実質休館」判定）
  - `kind="special"` → `datekb=3` のみ返す（仮スケジュール対象判定。ただし現行コードでは参照は不要）
  - `kind=None` → 従来通り全て（互換確保）
- コメントを `datekb` の意味と合わせて更新

### 3. `hacomono_gateway.fetch_closed_days` の kind 対応

**責務**:

- 呼び出し側が「定休日のみ」を要求できるようにする

**実装の要点**:

- シグネチャ: `fetch_closed_days(*, club_code, sisetcd, year, month, kind: str = "fixed") -> set[date]`
- `calendar_query` からは `kind="fixed"` で呼び、「店舗が閉まっている日」のみ取得する
- 既存キャッシュのキーに `kind` を追加する（`(club_code, sisetcd, year, month, kind)`）

### 4. `observed_lesson_repo.list_by_date` 新規

**責務**:

- 指定日・指定店舗の観測済み lesson を生の行として取得する

**実装の要点**:

- 既存 `list_by_range` は `dict[tuple[date, str], dict]` を返すが、仮スケジュール生成には観測行をそのまま欲しい
- 新メソッド: `list_by_date(db_path, *, studio_id, studio_room_id, lesson_date) -> list[dict]`
  - その日に観測された `observed_lessons` 行を全て返す
  - 返却 dict には `start_time`, `program_id`, `program_name`, `instructor_id`, `instructor_name`, `studio_room_space_id`, `capacity` を含める
  - 同一 (date, time) に複数 program_id が観測されている場合は全て返す（カレンダーは時刻違いで並ぶので衝突しない）
- 将来の用途も考えられる（観測ベースの履歴表示等）ため汎用に作る

### 5. `calendar_query._build_tentative_lessons` 新規プライベートメソッド

**責務**:

- 指定日に対し、1 週前・2 週前・3 週前の `observed_lessons` を順に探して、見つかった時点でその週の lesson を "仮"として組み立てる

**実装の要点**:

- 引数: `studio: Studio`, `target_dates: set[date]`, `today: date`
- `target_dates` の各日について以下を実行:
  1. `lesson_date < today` はスキップ（過去日は仮データを出さない）
  2. ソース候補 `[target - 7d, target - 14d, target - 21d]` を順に評価
  3. 各候補日で `observed_lesson_repo.list_by_date(...)` を呼ぶ
  4. 観測があればそれを元に `Lesson` オブジェクト群を生成:
     - `studio_lesson_id = 0` とする（仮データのため。既存の公開月間由来と同じ慣習）
     - `lesson_date = target`（仮の日付を target に書き換え）
     - `start_time`, `end_time`, `program_id`, `program_name`, `instructor_*`, `capacity`, `studio_room_space_id` は観測値をコピー
     - `is_reservable = False`, `reservable_from = None`, `reservable_to = None`
     - `remaining_seats = None`
     - `state = LessonState.AVAILABLE`
     - `release_pending = True`
     - `tentative_source = <候補日>`
  5. 候補が見つかった時点で break（次の週は評価しない）
- 戻り値: `list[Lesson]`
- `end_time` が DB 未保存なら、`start_time` から推定するか、`None` で返す（UI が吸収する）

### 6. `calendar_query.build_week` の統合

**責務**:

- `fixed_closed_days` の取得を `kind="fixed"` 指定に変更
- 既存の reserve API 経路で算出する `missing_dates` から、`special_dates`（datekb=3 由来）を差し引かない（=従来の public_monthly fill にまかせる）。`special_dates` で埋まらなかった日は続く仮スケジュール補完にフォールバックする
- `out_of_range` 経路でも同様に、`fetch_public_monthly_week` で埋まらなかった日を仮スケジュール補完する
- 仮スケジュールで作った lesson には `annotate_recurring_state` を適用する（TARGET 化も従来通り働く）
- `CalendarWeek.closed_dates` には `fixed_closed_days` のみを入れる（datekb=3 は入れない）

**実装の要点**:

- 既存の `missing_dates` 算出式は `week_dates - covered_dates - set(closed_days)` だが、`closed_days` を `fixed_closed_days`（datekb=1 のみ）に変更する
- public_monthly fill を実行した後、その週の `covered` を再計算し、さらに残った `missing` を `_build_tentative_lessons` に渡す
- 仮 lesson は `_annotate_recurring_state` のみ通す（`_annotate_reserved_state` は reserve API 経路用で仮には不要）
- `_annotate_intent_state` は最後に通す（仮 lesson にも intent があれば印が付く）

### 7. UI テンプレートの調整

**責務**:

- 「休館」バッジ削除
- 「仮」バッジ追加
- 仮 lesson の見た目を薄いグレーに

**実装の要点**:

- `ui/templates/reserve_calendar.html` の日付ヘッダ部分:
  - `{% if is_closed %}cal-head--closed{% endif %}` および「休館」span を削除
  - `closed_set` の受け渡し自体は残すが、今は事実上空で UI 上は無視してよい（将来別用途があれば残置）
- lesson セル部分:
  - `{% if lesson.tentative_source %}lesson--tentative{% endif %}` クラスを追加
  - `{% if lesson.tentative_source %}<span class="lesson-badge-tentative">仮</span>{% endif %}` で「仮」バッジを表示
- CSS:
  - `.lesson--tentative` → 背景を薄いグレー（既存の `--pending-intent` や `--target` と被らないトーン）
  - `.lesson-badge-tentative` → 「仮」の小さいピル状バッジ
- 右パネルの Intent 登録フォーム出し分け:
  - 条件式はすでに `{% elif view.out_of_range or lesson.release_pending %}` なので、仮 lesson（`release_pending=True`）は自動的に登録フォームが出る。変更不要
  - 説明文は「仮スケジュール（過去 X 週前のデータから推定）に対して予約予定を登録します」のような注意書きを追加してもよい（任意）

### 8. 凡例の更新

- `.legend` 内に「仮スケジュール」の swatch を追加する（`.legend-swatch--tentative` を新設）
- 「予約不可（満席・期限切れ・開催済）」などの既存凡例はそのまま

## データフロー

```
[カレンダー表示]
GET /reserve?week=YYYY-MM-DD
  → calendar_query.build_week(studio, week_start, today)
    1. fetch_closed_days(kind="fixed") → fixed_closed_days
    2. 範囲内: reserve API → lessons, public_monthly fill（datekb=3 の日も missing として埋まりうる）
       範囲外: public_monthly → lessons
    3. missing_dates = week_dates - covered - fixed_closed_days, today 以降に限る
    4. _build_tentative_lessons(studio, missing_dates, today)
       - 各 target 日: list_by_date(target - 7d) → observed なら生成、なければ -14d、-21d
       - 見つかれば Lesson(release_pending=True, tentative_source=<候補日>) を生成
    5. lessons に extend、annotate_recurring / annotate_intent
  → render reserve_calendar.html

[Intent 登録]
右パネルで仮 lesson を選択 → POST /reserve/intent （既存ルート、変更なし）
  → IntentRepository.insert(...)
  → 9:00 に auto_booking ジョブが拾って予約
```

## エラーハンドリング

- `list_by_date` で DB 読込失敗 → 例外を伝播せず空 list を返してログに警告（UI は空欄になるだけ）
- `observed_lessons` に該当週のデータが 1 行もない（初回起動や DB リセット直後） → 仮スケジュールは空、UI は空欄のまま。従来の挙動と変わらない
- `fetch_closed_days` が失敗 → 既存通り空 set を返し、UI は全日空欄（ただし休館表記自体をなくしたので大差ない）
- 仮 lesson が同じ (date, time) に既存 lesson と衝突 → 実データ優先（仮は追加しない）。`covered` から missing を計算した段階で既に弾かれているので通常は起きないが、防御的に `_build_tentative_lessons` 側でも covered を再確認する

## テスト方針

- ユニットテスト: 既存の方針を踏襲して新規ユニットテストは最小限。ただし仮スケジュール生成ロジック（`_build_tentative_lessons` に相当する関数）は純粋関数に近いため、pytest の既存スタイルで簡単なケースを 1〜2 本書く:
  - 1 週前に観測あり → 仮 lesson が 1 週前のデータで作られる
  - 1 週前に観測なし・2 週前にあり → 2 週前のデータで作られる
  - 3 週前まで無し → 空配列
- 統合テスト: スコープ外
- 動作確認: 実環境で以下を chromium スクショで確認:
  1. `/reserve` を開き、datekb=3 の日（2026-04-29）のセルに仮レッスンが並ぶ
  2. 「仮」バッジがついていて、薄いグレー表示になっている
  3. 仮 lesson をクリックすると右パネルに Intent 登録フォームが出る
  4. 金曜（datekb=1）の日は空欄のまま、「休館」バッジが出ない
  5. 翌月の未配信日（適切な週に移動）も仮スケジュールで埋まる
  6. Intent を登録した仮 lesson を再読込すると「◎」マーク + 「予約実行待ち」表示に切り替わる

## ファイル構成

**新規**:
- なし

**変更**:
- `app/domain/entities.py` — `Lesson` に `tentative_source: date | None = None` 追加
- `app/adapters/public_monthly_mapper.py` — `collect_closed_dates` に `kind` パラメータ追加、datekb=1/3 区別
- `app/adapters/hacomono_gateway.py` — `fetch_closed_days` に `kind` パラメータ追加、キャッシュキーに kind 追加
- `app/services/calendar_query.py`
  - `_fetch_closed_days` を `kind="fixed"` で呼ぶよう変更
  - `_build_tentative_lessons` 新規プライベートメソッド
  - `build_week` で reserve 経路と out_of_range 経路の両方に仮スケジュール補完を適用
- `db/repositories/observed_lesson_repo.py` — `list_by_date` 新規メソッド追加
- `ui/templates/reserve_calendar.html`
  - 休館バッジ削除（`cal-head--closed` とバッジ span）
  - lesson セルに「仮」バッジと `lesson--tentative` クラス
  - 凡例に「仮スケジュール」追加
- `ui/static/styles.css`（あるいは該当スタイルシート）
  - `.lesson--tentative`, `.lesson-badge-tentative`, `.legend-swatch--tentative` を追加

## 実装の順序

1. `Lesson` エンティティに `tentative_source` 追加（スキーマ変更ではないため単独で無害）
2. `collect_closed_dates` と `fetch_closed_days` の kind 対応 + calendar_query 側の呼び出し調整
3. `observed_lesson_repo.list_by_date` 追加
4. `calendar_query._build_tentative_lessons` 実装 + `build_week` に組み込み
5. テンプレートの「休館」削除 + 「仮」バッジ + CSS
6. 凡例の更新
7. ユニットテスト（仮スケジュール生成ロジック 1〜2 本）
8. ruff check
9. ビルド + `gateway docker --project central-sports-web compose up -d web`
10. chromium で実環境スクショ
