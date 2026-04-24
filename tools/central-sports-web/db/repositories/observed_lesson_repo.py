"""reserve API で観測したレッスン情報の永続化リポジトリ。

- reserve API は「真の表示名」（全角カナ・フルネーム instructor 等）を返すが、
  今日〜+6 日の窓しか取得できない。
- 公開月間 API は過去/未来週も取得できるが表記が揺れる。
- reserve API で観測したレッスンを `observed_lessons` に蓄積し、
  後に公開月間 API 経由で組み立てた lesson に対して同じ枠の観測値を
  優先適用することで、揺れた表記を正しい表記に差し替える。

マッチキーは `(lesson_date, start_time)` とし、CS Live 系（名前違いでも
時間帯は同じ）もカバーする。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from app.domain.entities import Lesson
from db.connection import read_connection, write_transaction


def upsert_many(db_path: Path, lessons: list[Lesson]) -> int:
    """reserve API 由来の lesson を一括 upsert する。

    1 トランザクションで executemany するので、N 件分の write を 1 回にまとめる。

    - `studio_lesson_id == 0` の lesson（= 公開月間 API 由来）は対象外。
      これを入れると公開月間由来の揺れた表記で `observed_lessons` が汚染される。
    - program_id が空文字の lesson は PRIMARY KEY の制約上 skip する。

    Returns: 書き込んだ件数。
    """

    rows: list[tuple] = []
    for lesson in lessons:
        if not lesson.studio_lesson_id:
            # 公開月間由来は対象外
            continue
        program_id = str(lesson.program_id or "")
        if not program_id:
            continue
        rows.append(
            (
                int(lesson.studio_id),
                int(lesson.studio_room_id),
                lesson.lesson_date.isoformat(),
                str(lesson.start_time),
                program_id,
                lesson.program_name,
                lesson.instructor_id,
                lesson.instructor_name,
                lesson.studio_room_space_id,
                lesson.capacity,
            )
        )

    if not rows:
        return 0

    with write_transaction(db_path) as con:
        con.executemany(
            """
            INSERT INTO observed_lessons
                (studio_id, studio_room_id, lesson_date, start_time, program_id,
                 program_name, instructor_id, instructor_name,
                 studio_room_space_id, capacity, observed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(studio_id, studio_room_id, lesson_date, start_time, program_id)
            DO UPDATE SET
                program_name = excluded.program_name,
                instructor_id = excluded.instructor_id,
                instructor_name = excluded.instructor_name,
                studio_room_space_id = excluded.studio_room_space_id,
                capacity = excluded.capacity,
                observed_at = excluded.observed_at
            """,
            rows,
        )
    return len(rows)


def list_by_range(
    db_path: Path,
    *,
    studio_id: int,
    studio_room_id: int,
    start: date,
    end: date,
) -> dict[tuple[date, str], dict]:
    """期間内の `observed_lessons` を取得して merge 用の dict を返す。

    Returns: {(lesson_date, start_time): {program_name, instructor_name,
             studio_room_space_id, program_id, instructor_id, capacity,
             observed_at}}

    同じ (date, time) に複数の program_id が観測されていた場合は
    観測時刻が新しいものを採用する（`observed_at DESC`）。
    """

    result: dict[tuple[date, str], dict] = {}
    with read_connection(db_path) as con:
        rows = con.execute(
            """
            SELECT lesson_date, start_time, program_id, program_name,
                   instructor_id, instructor_name, studio_room_space_id,
                   capacity, observed_at
            FROM observed_lessons
            WHERE studio_id = ?
              AND studio_room_id = ?
              AND lesson_date BETWEEN ? AND ?
            ORDER BY observed_at ASC
            """,
            (
                int(studio_id),
                int(studio_room_id),
                start.isoformat(),
                end.isoformat(),
            ),
        ).fetchall()

    for row in rows:
        try:
            d = date.fromisoformat(str(row["lesson_date"]))
        except (TypeError, ValueError):
            continue
        key = (d, str(row["start_time"] or ""))
        # ASC で舐めて上書きするので、最終的に最も新しい観測値が残る
        result[key] = {
            "program_id": row["program_id"],
            "program_name": row["program_name"],
            "instructor_id": row["instructor_id"],
            "instructor_name": row["instructor_name"],
            "studio_room_space_id": row["studio_room_space_id"],
            "capacity": row["capacity"],
            "observed_at": row["observed_at"],
        }
    return result


def list_by_date(
    db_path: Path,
    *,
    studio_id: int,
    studio_room_id: int,
    lesson_date: date,
) -> list[dict]:
    """指定日の観測済み lesson を start_time 昇順の list で返す。

    `list_by_range` は `(date, time)` キーの dict を返し同じ (date, time)
    で複数 program_id を畳み込んでしまうが、こちらは観測行をそのまま全件
    返す。仮スケジュール生成（`calendar_query._build_tentative_lessons`）
    が過去日のレッスン一覧を取り出すために使う。

    Returns: list[dict] — 各要素に以下のキーを含む:
      - `start_time`
      - `program_id`, `program_name`
      - `instructor_id`, `instructor_name`
      - `studio_room_space_id`
      - `capacity`
      - `observed_at`
    """

    with read_connection(db_path) as con:
        rows = con.execute(
            """
            SELECT start_time, program_id, program_name,
                   instructor_id, instructor_name, studio_room_space_id,
                   capacity, observed_at
            FROM observed_lessons
            WHERE studio_id = ?
              AND studio_room_id = ?
              AND lesson_date = ?
            ORDER BY start_time ASC
            """,
            (
                int(studio_id),
                int(studio_room_id),
                lesson_date.isoformat(),
            ),
        ).fetchall()

    return [
        {
            "start_time": str(row["start_time"] or ""),
            "program_id": row["program_id"],
            "program_name": row["program_name"],
            "instructor_id": row["instructor_id"],
            "instructor_name": row["instructor_name"],
            "studio_room_space_id": row["studio_room_space_id"],
            "capacity": row["capacity"],
            "observed_at": row["observed_at"],
        }
        for row in rows
    ]


__all__ = ["upsert_many", "list_by_range", "list_by_date"]
