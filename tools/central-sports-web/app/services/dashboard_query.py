"""ダッシュボード画面向けの集計ユースケース。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from app.domain.entities import (
    BookingIntent,
    DailySummary,
    DailySummaryItem,
    DailySummaryStatus,
    HistoryCategory,
    HistoryEntry,
    HistoryResult,
    IntentStatus,
    ProgramChangeAlert,
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

if TYPE_CHECKING:
    from app.services.reserve_recurring import RecurringService

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class DashboardData:
    today: date
    daily_summary: DailySummary
    current_reservations: list[Reservation]
    upcoming: list[UpcomingReservation]
    history: list[HistoryEntry]
    program_changes: list[ProgramChangeAlert] = field(default_factory=list)


class DashboardQueryService:
    def __init__(
        self,
        db_path: Path,
        settings: Settings,
        recurring_service: "RecurringService | None" = None,
    ) -> None:
        self._db_path = db_path
        self._settings = settings
        self._recurring_service = recurring_service

    def build(self, *, today: date, now: datetime) -> DashboardData:
        start_of_day = datetime.combine(today, time(0, 0))
        end_of_day = datetime.combine(today, time(23, 59, 59))
        todays_records = history_repo.list_between(
            self._db_path,
            start=start_of_day,
            end=end_of_day,
            categories=[
                HistoryCategory.AUTOMATION,
                HistoryCategory.MANUAL,
                # マイページ側でキャンセルされた予約は sync 経路で
                # reservation.cancel 履歴として残る。これもサマリーに反映させる。
                HistoryCategory.SYNC_MY_RESERVATIONS,
            ],
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

        program_changes = self._collect_program_changes(
            recurring_items=recurring_items, today=today
        )

        return DashboardData(
            today=today,
            daily_summary=summary,
            current_reservations=current,
            upcoming=upcoming,
            history=recent_history,
            program_changes=program_changes,
        )

    def _collect_program_changes(
        self,
        *,
        recurring_items: list[RecurringReservation],
        today: date,
    ) -> list[ProgramChangeAlert]:
        """定期予約の今後 4 週の配置プレビューから、差分ありかつ未予約の通知を集める。

        reserve API / 公開月間 API を叩くため、失敗時は警告ログだけ出して空を返す。
        ダッシュボードの他機能を巻き込まない。
        """

        if self._recurring_service is None:
            return []
        alerts: list[ProgramChangeAlert] = []
        for item in recurring_items:
            if item.status is not RecurringStatus.ACTIVE:
                continue
            try:
                occurrences = self._recurring_service.build_occurrences(
                    item, today=today, weeks=4
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "dashboard: build_occurrences failed for recurring %s: %s",
                    item.id, exc,
                )
                continue
            for occ in occurrences:
                if not occ.diff_flags:
                    continue
                if occ.status == "reserved":
                    continue
                expected = item.program_name
                actual = occ.program_name
                # プログラム名が None / 空文字列のときは変更検知から除外
                if not expected or not actual:
                    continue
                alerts.append(
                    ProgramChangeAlert(
                        recurring_id=item.id,
                        lesson_date=occ.lesson_date,
                        lesson_time=occ.lesson_time,
                        expected_name=expected,
                        actual_name=actual,
                        diff_flags=list(occ.diff_flags),
                        alert_message=occ.alert_message,
                    )
                )
        alerts.sort(key=lambda a: (a.lesson_date, a.lesson_time))
        return alerts

    # --- 内部 ------------------------------------------------------

    def _summarize(
        self,
        records: list[HistoryEntry],
        *,
        today: date,
    ) -> DailySummary:
        """今朝の予約結果を 1 件 1 カード方式で集計する。

        同じ (lesson_date, lesson_time, program_id) で reserve 成功 → cancel 成功
        となったペアも、対消滅させずに「予約後に取消」ステータスの
        カードとして items に残す。move（席変更）は結果に影響しないため無視する。
        """

        reserve_endpoints = {"reservation.create", "reservation.reserve"}
        cancel_endpoints = {"reservation.cancel"}

        def _key(meta: dict | None) -> tuple[str, str, str]:
            meta = meta or {}
            return (
                str(meta.get("lesson_date", "")),
                str(meta.get("lesson_time", "")),
                str(meta.get("program_id", "")),
            )

        # 取消（成功）だけ数え、あとで reserve 成功とペアリングする
        cancel_counts: dict[tuple[str, str, str], int] = {}
        for rec in records:
            if rec.endpoint in cancel_endpoints and rec.result is HistoryResult.SUCCESS:
                k = _key(rec.metadata)
                cancel_counts[k] = cancel_counts.get(k, 0) + 1

        # reserve 系を時系列順に見て、成功のうちキャンセルされたものを識別
        reserves_sorted = sorted(
            (r for r in records if r.endpoint in reserve_endpoints),
            key=lambda r: r.occurred_at,
        )

        # 同じ予約キー（date+time+program_id）の最終状態だけを 1 カードにまとめる。
        # 同じレッスンを何度も reserve/cancel 繰り返しても、サマリーは 1 行で表現する。
        items_by_key: dict[tuple[str, str, str], DailySummaryItem] = {}
        for rec in reserves_sorted:
            k = _key(rec.metadata)
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

            # ステータスを確定（件数は最後に items_by_key から集計する）
            if rec.result is HistoryResult.SUCCESS and cancel_counts.get(k, 0) > 0:
                # 予約成功 → 取消ペア: CANCELLED_AFTER_RESERVE
                cancel_counts[k] -= 1
                status = DailySummaryStatus.CANCELLED_AFTER_RESERVE
                detail = "予約後に取り消しました"
            elif rec.result is HistoryResult.SUCCESS:
                status = DailySummaryStatus.RESERVED
                detail = "予約成功"
            elif rec.result is HistoryResult.WARNING:
                status = DailySummaryStatus.WARNING
                detail = "代替席で予約"
            else:
                status = DailySummaryStatus.FAILED
                detail = rec.message or "予約できませんでした"

            # 同一キーの後続イベントで上書き（最終状態が残る）
            items_by_key[k] = DailySummaryItem(
                program_name=program_name,
                seat_no=seat_no,
                lesson_date=lesson_date,
                lesson_time=lesson_time,
                result=rec.result,
                detail=detail,
                status=status,
            )

        items = sorted(
            items_by_key.values(),
            key=lambda it: (it.lesson_date, it.lesson_time),
        )
        success = sum(1 for it in items if it.status is DailySummaryStatus.RESERVED)
        warning = sum(1 for it in items if it.status is DailySummaryStatus.WARNING)
        failure = sum(1 for it in items if it.status is DailySummaryStatus.FAILED)
        cancelled = sum(
            1 for it in items if it.status is DailySummaryStatus.CANCELLED_AFTER_RESERVE
        )
        return DailySummary(
            date=today,
            success_count=success,
            warning_count=warning,
            failure_count=failure,
            items=items,
            cancelled_count=cancelled,
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
                # schedule_open_days=7 は「今日〜今日+6 日」の 7 日間開放なので、
                # lesson_date に対する予約実行タイミングは lesson_date - 6 日の 9:00
                run_at = datetime.combine(
                    lesson_date - timedelta(days=6),
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
                        intent.lesson_date - timedelta(days=6), time(run_hour, run_minute)
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
