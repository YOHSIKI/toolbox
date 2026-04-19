"""カレンダー画面で必要な週次スケジュール組み立て。

- gateway で生のレッスン一覧を取得（本番は hacomono、dev モードではフェイク）
- ローカルの予約・定期予約とマージして LessonState を決定
- 右側パネル用に選択中レッスンの SeatMap を取得
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from app.domain.entities import (
    CalendarCell,
    CalendarWeek,
    Lesson,
    LessonState,
    RecurringStatus,
    SeatMap,
    Studio,
)
from app.domain.ports import ReservationGateway
from config.settings import Settings
from db.repositories import recurring_repo, reservation_repo

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class Selection:
    program_id: str | None = None
    lesson_time: str | None = None
    lesson_date: date | None = None


class CalendarQueryService:
    """カレンダー描画向けのユースケース。"""

    def __init__(
        self,
        db_path: Path,
        gateway: ReservationGateway,
        *,
        hour_range: tuple[int, int] = (9, 21),
        settings: Settings | None = None,
    ) -> None:
        self._db_path = db_path
        self._gateway = gateway
        self._hour_range = hour_range
        self._settings = settings

    def build_week(
        self,
        studio: Studio,
        week_start: date,
        today: date,
        selection: Selection | None = None,
    ) -> CalendarWeek:
        # 予約 API の公開範囲（今日〜+6 日）内は reserve API、範囲外は公開月間 API
        open_until = today + timedelta(days=6)
        out_of_range = week_start > open_until
        lessons: list[Lesson] = []
        if not out_of_range:
            lessons = self._gateway.fetch_week(studio.ref, week_start, days=7)
            self._annotate_reserved_state(lessons)
        else:
            lessons = self._fetch_public_monthly_week(studio, week_start)
            self._annotate_reserved_state(lessons)
        logger.info(
            "build_week studio=%s week=%s out_of_range=%s lessons=%d",
            studio.display_name, week_start, out_of_range, len(lessons),
        )

        settings_hours = (
            self._settings.calendar_start_hour if self._settings else self._hour_range[0],
            self._settings.calendar_end_hour if self._settings else self._hour_range[1],
        )
        hours = list(range(settings_hours[0], settings_hours[1] + 1))
        days = [week_start + timedelta(days=i) for i in range(7)]
        cell_map = self._to_cell_map(lessons, hours=hours, days=days)
        rows = [
            [
                CalendarCell(hour=h, weekday=w, lessons=cell_map.get((h, w), []))
                for w in range(len(days))
            ]
            for h in hours
        ]

        selected = self._pick_selection(lessons, selection or Selection())
        seat_map = self._resolve_seat_map(selected) if selected is not None else None

        return CalendarWeek(
            studio=studio,
            week_start=week_start,
            week_end=week_start + timedelta(days=len(days) - 1),
            days=days,
            hours=hours,
            rows=rows,
            selected_lesson=selected,
            selected_seat_map=seat_map,
            today=today,
            open_days=7,
            open_until=open_until,
            out_of_range=out_of_range,
        )

    def _fetch_public_monthly_week(
        self,
        studio: Studio,
        week_start: date,
    ) -> list[Lesson]:
        """予約可能範囲外の週を、公開月間 API（閲覧専用）から取得する。"""

        logger.info(
            "public monthly: studio=%s club_code=%s sisetcd=%s week_start=%s",
            studio.display_name, studio.club_code, studio.sisetcd, week_start,
        )
        if not studio.club_code or not studio.sisetcd:
            logger.warning(
                "public monthly skipped: studio %s has no club_code/sisetcd",
                studio.display_name,
            )
            return []
        week_end = week_start + timedelta(days=6)
        months: set[tuple[int, int]] = set()
        cursor = week_start
        while cursor <= week_end:
            months.add((cursor.year, cursor.month))
            cursor += timedelta(days=1)
        results: list[Lesson] = []
        for year, month in sorted(months):
            try:
                month_lessons = self._gateway.fetch_monthly_public(  # type: ignore[attr-defined]
                    club_code=studio.club_code,
                    sisetcd=studio.sisetcd,
                    studio_id=studio.studio_id,
                    studio_room_id=studio.studio_room_id,
                    year=year,
                    month=month,
                    week_range=(week_start, week_end),
                )
                logger.info(
                    "public monthly fetched year=%d month=%d lessons=%d",
                    year, month, len(month_lessons),
                )
                results.extend(month_lessons)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "public monthly fetch failed year=%d month=%d: %s",
                    year, month, exc,
                )
        return results

    # --- 内部 -------------------------------------------------------

    def _annotate_reserved_state(self, lessons: list[Lesson]) -> None:
        reservations = reservation_repo.list_reservations(self._db_path)
        reservation_index = {
            (r.studio_lesson_id, r.lesson_date, r.lesson_time): r
            for r in reservations
            if r.studio_lesson_id
        }
        recurring_items = recurring_repo.list_recurring(self._db_path)
        recurring_index = {
            (
                r.day_of_week,
                r.start_time,
                r.program_id,
                r.studio_id,
                r.studio_room_id,
            ): r
            for r in recurring_items
            if r.status is RecurringStatus.ACTIVE
        }
        for lesson in lessons:
            key = (lesson.studio_lesson_id, lesson.lesson_date, lesson.start_time)
            reservation = reservation_index.get(key)
            if reservation is not None:
                lesson.state = LessonState.RESERVED
                lesson.reserved_seat_no = reservation.seat_no
                lesson.reserved_origin = reservation.origin
                lesson.reserved_reservation_id = reservation.id
                continue
            recur_key = (
                lesson.lesson_date.weekday(),
                lesson.start_time,
                lesson.program_id,
                lesson.studio_id,
                lesson.studio_room_id,
            )
            if recur_key in recurring_index:
                lesson.state = LessonState.TARGET
                continue
            if lesson.remaining_seats == 0:
                lesson.state = LessonState.FULL
            elif not lesson.is_reservable:
                lesson.state = LessonState.UNRESERVABLE
            else:
                lesson.state = LessonState.AVAILABLE

    def _to_cell_map(
        self,
        lessons: list[Lesson],
        *,
        hours: list[int],
        days: list[date],
    ) -> dict[tuple[int, int], list[Lesson]]:
        """(hour, column_index) ごとに複数レッスンを積める辞書を作る。

        column_index は days のインデックス（0 がカレンダー左端）。
        week_start を任意日付にできるよう、weekday() ではなく列位置で引く。
        """

        date_to_col = {d: i for i, d in enumerate(days)}
        hour_set = set(hours)
        cells: dict[tuple[int, int], list[Lesson]] = {}
        for lesson in lessons:
            col = date_to_col.get(lesson.lesson_date)
            if col is None:
                continue
            hour = int(lesson.start_time.split(":")[0])
            if hour not in hour_set:
                continue
            cells.setdefault((hour, col), []).append(lesson)
        for items in cells.values():
            items.sort(key=lambda ls: ls.start_time)
        return cells

    def _pick_selection(
        self,
        lessons: list[Lesson],
        selection: Selection,
    ) -> Lesson | None:
        """クエリで明示的に選択されたレッスンのみ返す。未指定時は None。"""

        if not (selection.lesson_date and selection.program_id and selection.lesson_time):
            return None
        for lesson in lessons:
            if (
                lesson.lesson_date == selection.lesson_date
                and lesson.program_id == selection.program_id
                and lesson.start_time == selection.lesson_time
            ):
                return lesson
        return None

    def _resolve_seat_map(self, lesson: Lesson) -> SeatMap | None:
        try:
            return self._gateway.fetch_seat_map(
                lesson.studio_lesson_id,
                capacity_hint=lesson.capacity,
            )
        except Exception as exc:  # noqa: BLE001 - UI を落とさない
            logger.warning(
                "seat map fetch failed for lesson_id=%d: %s",
                lesson.studio_lesson_id,
                exc,
            )
            return None


def resolve_week_start(raw: str | None, *, today: date) -> date:
    """?week=YYYY-MM-DD をパースする。未指定なら今日を起点にする。

    monday 丸めはせず、指定日から 7 日間を並べる（今日を一番左に）。
    """

    if raw:
        try:
            return date.fromisoformat(raw)
        except ValueError:
            pass
    return today
