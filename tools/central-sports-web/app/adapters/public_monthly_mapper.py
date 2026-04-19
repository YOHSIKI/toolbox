"""公開月間 API のレスポンスを `Lesson` のリストに変換する。

- `pims_schedule` は「曜日 × 時刻」の週間パターン
- 指定した月の各日付に対し、曜日一致するレッスンを配置する
- `pims_closed` に載っている日は休館扱いで除外
- 予約可否は API 側では判定できないため、`state = UNRESERVABLE`（閲覧専用）で返す
"""

from __future__ import annotations

import calendar as _calendar
from datetime import date

from app.domain.entities import Lesson, LessonState
from infra.hacomono.public_monthly import PublicMonthlyPayload


def _youbi_of_date(d: date) -> str:
    """公開 API の youbi 形式に合わせる（1=月 ... 7=日）。"""

    return str(d.weekday() + 1)


def _hhmm(sttime: str | int | None) -> str | None:
    """'850' → '08:50'、'1115' → '11:15'、'915' → '09:15' に整形する。"""

    if sttime is None:
        return None
    text = str(sttime).strip()
    if not text:
        return None
    text = text.zfill(4)
    try:
        hh = int(text[:-2])
        mm = int(text[-2:])
        if not (0 <= hh < 24 and 0 <= mm < 60):
            return None
        return f"{hh:02d}:{mm:02d}"
    except ValueError:
        return None


def _end_hhmm(start: str | None, duration_min: str | int | None) -> str | None:
    if start is None or duration_min is None:
        return None
    try:
        mins = int(duration_min)
    except (TypeError, ValueError):
        return None
    hh, mm = start.split(":")
    total = int(hh) * 60 + int(mm) + mins
    if total >= 24 * 60:
        total %= 24 * 60
    return f"{total // 60:02d}:{total % 60:02d}"


def _month_iter(year: int, month: int):
    last_day = _calendar.monthrange(year, month)[1]
    for day in range(1, last_day + 1):
        yield date(year, month, day)


def map_public_monthly(
    payload: PublicMonthlyPayload,
    *,
    year: int,
    month: int,
    sisetcd: str,
    studio_id: int,
    studio_room_id: int,
    week_range: tuple[date, date] | None = None,
) -> list[Lesson]:
    """月間データから `Lesson` リストを生成する。

    week_range を指定すると、その範囲の日付だけに絞って返す。
    """

    # 休館日集合（pims_closed は全日メタで、datekb != '0' が休館扱い）
    closed_dates: set[date] = set()
    for entry in payload.closed_days:
        day_raw = entry.get("datebi")
        try:
            day = int(day_raw) if day_raw is not None else None
        except (TypeError, ValueError):
            day = None
        if day is None:
            continue
        datekb = str(entry.get("datekb") or "0")
        if datekb == "0":
            continue  # 営業日
        try:
            closed_dates.add(date(year, month, day))
        except ValueError:
            continue

    # 曜日 → 指定施設のレッスン候補
    by_youbi: dict[str, list[dict]] = {}
    for row in payload.schedule:
        if str(row.get("sisetcd") or "") != sisetcd:
            continue
        youbi = str(row.get("youbi") or "")
        if not youbi:
            continue
        by_youbi.setdefault(youbi, []).append(row)

    lessons: list[Lesson] = []
    for day in _month_iter(year, month):
        if week_range is not None:
            start, end = week_range
            if day < start or day > end:
                continue
        if day in closed_dates:
            continue
        candidates = by_youbi.get(_youbi_of_date(day), [])
        for row in candidates:
            # スクール系（キッズ・合気道等、長期契約/別運用）は除外
            if str(row.get("school") or "0") == "1":
                continue
            program_name_raw = str(row.get("prognm") or "")
            # 予約不要のスクール（yoyakb=1）や、名前が「スクール」で終わるものも除外
            if str(row.get("yoyakb") or "0") == "1":
                continue
            if "スクール" in program_name_raw:
                continue
            start_time = _hhmm(row.get("sttime"))
            if start_time is None:
                continue
            end_time = _end_hhmm(start_time, row.get("totime"))
            program_id = str(row.get("progcd") or "")
            program_name = program_name_raw or program_id or "レッスン"
            instructor_name = row.get("insnm") or row.get("instnm")
            if isinstance(instructor_name, str):
                instructor_name = instructor_name.strip() or None
            else:
                instructor_name = None
            lesson = Lesson(
                studio_lesson_id=0,  # 公開月間には lesson id が無い。予約は不可
                studio_id=studio_id,
                studio_room_id=studio_room_id,
                lesson_date=day,
                start_time=start_time,
                end_time=end_time,
                program_id=program_id,
                program_name=program_name,
                instructor_id=None,
                instructor_name=instructor_name,
                capacity=None,
                remaining_seats=None,
                studio_room_space_id=None,
                space_layout_name=None,
                is_reservable=False,
                reservable_from=None,
                reservable_to=None,
                # 未開放週は「空きあり」相当の見た目でカレンダーに描画し、パネル側で
                # 予約予定フォームに切り替える。is_reservable=False だけは維持
                state=LessonState.AVAILABLE,
            )
            lessons.append(lesson)

    lessons.sort(key=lambda lsn: (lsn.lesson_date, lsn.start_time))
    return lessons
