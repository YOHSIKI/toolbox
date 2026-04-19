"""ダッシュボード画面向けの集計ユースケース。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

from app.domain.entities import (
    BookingIntent,
    DailySummary,
    DailySummaryItem,
    HistoryCategory,
    HistoryEntry,
    HistoryResult,
    IntentStatus,
    RecurringReservation,
    RecurringStatus,
    Reservation,
    ReservationStatus,
    UpcomingReservation,
    next_weekday,
)
from config.settings import Settings
from db.repositories import (
    history_repo,
    intent_repo,
    recurring_repo,
    reservation_repo,
)


@dataclass(slots=True)
class DashboardData:
    today: date
    daily_summary: DailySummary
    current_reservations: list[Reservation]
    upcoming: list[UpcomingReservation]
    history: list[HistoryEntry]


class DashboardQueryService:
    def __init__(self, db_path: Path, settings: Settings) -> None:
        self._db_path = db_path
        self._settings = settings

    def build(self, *, today: date, now: datetime) -> DashboardData:
        start_of_day = datetime.combine(today, time(0, 0))
        end_of_day = datetime.combine(today, time(23, 59, 59))
        todays_records = history_repo.list_between(
            self._db_path,
            start=start_of_day,
            end=end_of_day,
            categories=[HistoryCategory.AUTOMATION, HistoryCategory.MANUAL],
        )
        summary = self._summarize(todays_records, today=today)

        current = reservation_repo.list_reservations(
            self._db_path,
            status=ReservationStatus.CONFIRMED,
            since=today,
        )

        recurring_items = recurring_repo.list_recurring(self._db_path)
        intents = intent_repo.list_intents(
            self._db_path, status=IntentStatus.PENDING, pending_from=today
        )
        upcoming = self._build_upcoming(
            recurring_items=recurring_items,
            reservations=current,
            today=today,
            intents=intents,
        )

        recent_history = history_repo.list_recent(
            self._db_path,
            limit=30,
            categories=[HistoryCategory.AUTOMATION, HistoryCategory.MANUAL],
        )

        return DashboardData(
            today=today,
            daily_summary=summary,
            current_reservations=current,
            upcoming=upcoming,
            history=recent_history,
        )

    # --- 内部 ------------------------------------------------------

    def _summarize(
        self,
        records: list[HistoryEntry],
        *,
        today: date,
    ) -> DailySummary:
        success = warning = failure = 0
        items: list[DailySummaryItem] = []
        for rec in records:
            # 取消・席変更はサマリー対象外（「予約結果」ではないため）
            if rec.endpoint in {"reservation.cancel", "reservation.move"}:
                continue
            metadata = rec.metadata or {}
            program_name = str(metadata.get("program_name", "レッスン"))
            seat_no_raw = metadata.get("seat_no")
            seat_no = None
            if seat_no_raw is not None:
                try:
                    seat_no = int(seat_no_raw)
                except (TypeError, ValueError):
                    seat_no = None
            lesson_date = today
            lesson_date_str = metadata.get("lesson_date")
            if lesson_date_str:
                try:
                    lesson_date = date.fromisoformat(str(lesson_date_str))
                except ValueError:
                    pass
            lesson_time = str(metadata.get("lesson_time", "09:00"))
            detail = rec.message or ""
            if rec.result is HistoryResult.SUCCESS:
                success += 1
            elif rec.result is HistoryResult.WARNING:
                warning += 1
            else:
                failure += 1
            items.append(
                DailySummaryItem(
                    program_name=program_name,
                    seat_no=seat_no,
                    lesson_date=lesson_date,
                    lesson_time=lesson_time,
                    result=rec.result,
                    detail=detail,
                )
            )
        return DailySummary(
            date=today,
            success_count=success,
            warning_count=warning,
            failure_count=failure,
            items=items,
        )

    def _build_upcoming(
        self,
        *,
        recurring_items: list[RecurringReservation],
        reservations: list[Reservation],
        today: date,
        intents: list[BookingIntent] | None = None,
    ) -> list[UpcomingReservation]:
        occupied = {
            (r.lesson_date, r.lesson_time, r.program_id, r.studio_id, r.studio_room_id)
            for r in reservations
        }
        run_hour = self._settings.run_hour
        run_minute = self._settings.run_minute
        results: list[UpcomingReservation] = []
        for item in recurring_items:
            if item.status is not RecurringStatus.ACTIVE:
                continue
            base = next_weekday(today, item.day_of_week)
            for week in range(4):
                lesson_date = base + timedelta(weeks=week)
                key = (
                    lesson_date,
                    item.start_time,
                    item.program_id,
                    item.studio_id,
                    item.studio_room_id,
                )
                if key in occupied:
                    continue
                run_at = datetime.combine(
                    lesson_date - timedelta(days=7),
                    time(run_hour, run_minute),
                )
                if run_at.date() < today:
                    continue
                results.append(
                    UpcomingReservation(
                        recurring_id=item.id,
                        lesson_date=lesson_date,
                        lesson_time=item.start_time,
                        program_name=item.program_name,
                        seat_preferences=list(item.seat_preferences),
                        scheduled_run_at=run_at,
                    )
                )
        # 予約予定（Intent）も upcoming に並べる
        for intent in intents or []:
            if intent.status is not IntentStatus.PENDING:
                continue
            if intent.lesson_date < today:
                continue
            key = (
                intent.lesson_date,
                intent.lesson_time,
                intent.program_id,
                intent.studio_id,
                intent.studio_room_id,
            )
            if key in occupied:
                continue
            results.append(
                UpcomingReservation(
                    recurring_id=f"intent:{intent.id}",
                    lesson_date=intent.lesson_date,
                    lesson_time=intent.lesson_time,
                    program_name=intent.program_name,
                    seat_preferences=list(intent.seat_preferences),
                    scheduled_run_at=intent.scheduled_run_at or datetime.combine(
                        intent.lesson_date - timedelta(days=7), time(run_hour, run_minute)
                    ),
                )
            )
        results.sort(key=lambda x: (x.scheduled_run_at, x.lesson_date, x.lesson_time))
        return results


def run_schedule_label(*, run_at: datetime, today: date) -> str:
    if run_at.date() == today:
        return f"今日 {run_at:%H:%M} に予約"
    if run_at.date() == today + timedelta(days=1):
        return f"明日 {run_at:%H:%M} に予約"
    return f"{run_at.month}/{run_at.day} {run_at:%H:%M} に予約"


def relative_log_time(entry_at: datetime, *, today: date) -> str:
    if entry_at.date() == today:
        return f"今朝 {entry_at:%H:%M}"
    if entry_at.date() == today - timedelta(days=1):
        return f"昨朝 {entry_at:%H:%M}"
    return entry_at.strftime("%m/%d")
