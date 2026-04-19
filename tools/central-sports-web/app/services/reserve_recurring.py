"""定期予約の CRUD と実行。

CRUD は DB への書き込みのみ。実行（`execute_one`）は 9:00 ジョブと手動トリガから呼ぶ。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

from app.domain.entities import (
    HistoryCategory,
    HistoryEntry,
    HistoryResult,
    Lesson,
    OccurrencePreview,
    RecurringReservation,
    RecurringStatus,
    Reservation,
    ReservationOrigin,
    ReservationStatus,
    StudioRef,
    next_weekday,
)
from app.domain.errors import NotFound
from app.domain.ports import ReservationGateway
from config.settings import Settings
from db.repositories import history_repo, recurring_repo, reservation_repo

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RecurringRunResult:
    recurring_id: str
    target_date: date
    ok: bool
    seat_no: int | None
    external_id: int | None
    message: str
    failure_reason: str | None = None


class RecurringService:
    def __init__(
        self,
        db_path: Path,
        gateway: ReservationGateway,
        settings: Settings,
    ) -> None:
        self._db_path = db_path
        self._gateway = gateway
        self._settings = settings

    # --- CRUD -----------------------------------------------------

    def create(
        self,
        *,
        day_of_week: int,
        start_time: str,
        program_id: str,
        program_name: str,
        studio_id: int,
        studio_room_id: int,
        seat_preferences: list[int],
        note: str | None = None,
    ) -> RecurringReservation:
        item = RecurringReservation(
            id=uuid.uuid4().hex,
            day_of_week=day_of_week,
            start_time=start_time,
            program_id=program_id,
            program_name=program_name,
            studio_id=studio_id,
            studio_room_id=studio_room_id,
            seat_preferences=seat_preferences,
            status=RecurringStatus.ACTIVE,
            note=note,
        )
        recurring_repo.insert_recurring(self._db_path, item)
        return item

    def update(self, item: RecurringReservation) -> None:
        recurring_repo.update_recurring(self._db_path, item)

    def set_status(self, recurring_id: str, status: RecurringStatus) -> None:
        recurring_repo.update_status(self._db_path, recurring_id, status)

    def get(self, recurring_id: str) -> RecurringReservation | None:
        return recurring_repo.get_recurring(self._db_path, recurring_id)

    def list_active(self) -> list[RecurringReservation]:
        return [
            r
            for r in recurring_repo.list_recurring(self._db_path)
            if r.status is RecurringStatus.ACTIVE
        ]

    def pick_default(
        self,
        preferred_id: str | None,
    ) -> RecurringReservation | None:
        items = recurring_repo.list_recurring(self._db_path)
        if preferred_id:
            for item in items:
                if item.id == preferred_id:
                    return item
        return items[0] if items else None

    # --- 配置プレビュー ---------------------------------------------

    def build_occurrences(
        self,
        item: RecurringReservation,
        *,
        today: date,
        weeks: int = 4,
    ) -> list[OccurrencePreview]:
        run_hour = self._settings.run_hour
        run_minute = self._settings.run_minute
        base = next_weekday(today, item.day_of_week)
        studio_ref = StudioRef(item.studio_id, item.studio_room_id)

        # 週ごとにスケジュール取得（重複する週はキャッシュ）
        weekly_cache: dict[date, list[Lesson]] = {}
        reservations = reservation_repo.list_reservations(
            self._db_path, since=today
        )
        reservation_index = {
            (r.lesson_date, r.lesson_time, r.program_id): r for r in reservations
        }

        results: list[OccurrencePreview] = []
        for week in range(weeks):
            lesson_date = base + timedelta(weeks=week)
            run_at = datetime.combine(
                lesson_date - timedelta(days=7),
                time(run_hour, run_minute),
            )
            week_start = lesson_date - timedelta(days=lesson_date.weekday())
            if week_start not in weekly_cache:
                try:
                    weekly_cache[week_start] = self._gateway.fetch_week(
                        studio_ref, week_start
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "occurrence preview: failed to fetch week=%s: %s",
                        week_start, exc,
                    )
                    weekly_cache[week_start] = []
            lessons = weekly_cache[week_start]

            match = self._match_lesson(lessons, lesson_date=lesson_date, item=item)
            reservation = reservation_index.get((lesson_date, item.start_time, item.program_id))

            diff_flags: list[str] = []
            alert_message: str | None = None
            instructor_name: str | None = match.instructor_name if match else None
            actual_time = match.start_time if match else item.start_time
            program_name = match.program_name if match else item.program_name

            if match is not None and match.start_time != item.start_time:
                diff_flags.append("時間変更")
                alert_message = f"時間が変更されています（{item.start_time} → {match.start_time}）"
            if match is not None and match.program_name != item.program_name:
                diff_flags.append("プログラム変更")

            status: str
            if reservation is not None:
                status = "reserved"
            elif match is None:
                status = "planned"
            elif diff_flags:
                status = "attention"
            elif run_at.date() <= today:
                status = "waiting"
            else:
                status = "planned"

            results.append(
                OccurrencePreview(
                    lesson_date=lesson_date,
                    lesson_time=actual_time,
                    program_name=program_name,
                    instructor_name=instructor_name,
                    scheduled_run_at=run_at,
                    status=status,  # type: ignore[arg-type]
                    seat_no=reservation.seat_no if reservation else None,
                    diff_flags=diff_flags,
                    alert_message=alert_message,
                )
            )
        return results

    # --- 実行 -----------------------------------------------------

    def execute_one(
        self,
        recurring_id: str,
        *,
        target_date: date,
        source_category: HistoryCategory = HistoryCategory.AUTOMATION,
    ) -> RecurringRunResult:
        item = self.get(recurring_id)
        if item is None:
            raise NotFound(f"recurring {recurring_id} not found")
        if item.status is not RecurringStatus.ACTIVE:
            return RecurringRunResult(
                recurring_id=recurring_id,
                target_date=target_date,
                ok=False,
                seat_no=None,
                external_id=None,
                message="定期予約が有効ではありません",
                failure_reason="NotActive",
            )

        # 冪等性: 同日に成功済みならスキップ
        already = self._already_reserved(item, target_date)
        if already is not None:
            return RecurringRunResult(
                recurring_id=recurring_id,
                target_date=target_date,
                ok=True,
                seat_no=already.seat_no,
                external_id=already.external_id,
                message="既に予約済みです",
            )

        lesson = self._resolve_target_lesson(item, target_date)
        if lesson is None:
            message = "対象レッスンを見つけられませんでした"
            self._record_history(
                item,
                target_date=target_date,
                result=HistoryResult.FAILURE,
                message=message,
                attempted_seats=item.seat_preferences,
                seat_no=None,
                failure_reason="LessonNotFound",
                category=source_category,
            )
            return RecurringRunResult(
                recurring_id=recurring_id,
                target_date=target_date,
                ok=False,
                seat_no=None,
                external_id=None,
                message=message,
                failure_reason="LessonNotFound",
            )

        started = datetime.now()
        attempt = self._gateway.attempt_reservation(
            studio_lesson_id=lesson.studio_lesson_id,
            no_preferences=item.seat_preferences,
        )
        elapsed_ms = int((datetime.now() - started).total_seconds() * 1000)

        if attempt.ok and attempt.external_id is not None:
            reservation = Reservation(
                id=uuid.uuid4().hex,
                external_id=attempt.external_id,
                studio_lesson_id=lesson.studio_lesson_id,
                lesson_date=lesson.lesson_date,
                lesson_time=lesson.start_time,
                program_id=lesson.program_id,
                program_name=lesson.program_name,
                instructor_name=lesson.instructor_name,
                studio_id=lesson.studio_id,
                studio_room_id=lesson.studio_room_id,
                seat_no=attempt.seat_no,
                origin=ReservationOrigin.RECURRING,
                origin_id=item.id,
                status=ReservationStatus.CONFIRMED,
            )
            reservation_repo.upsert_reservation(self._db_path, reservation)
            result = (
                HistoryResult.SUCCESS
                if attempt.succeeded_with_first_choice
                else HistoryResult.WARNING
            )
            message = attempt.message or (
                f"{lesson.program_name} を席 {attempt.seat_no:02d} で予約しました"
            )
        else:
            result = HistoryResult.FAILURE
            message = attempt.message or "予約に失敗しました"

        self._record_history(
            item,
            target_date=target_date,
            result=result,
            message=message,
            attempted_seats=item.seat_preferences,
            seat_no=attempt.seat_no,
            failure_reason=attempt.failure_reason,
            elapsed_ms=elapsed_ms,
            category=source_category,
            studio_lesson_id=lesson.studio_lesson_id,
        )

        return RecurringRunResult(
            recurring_id=recurring_id,
            target_date=target_date,
            ok=attempt.ok,
            seat_no=attempt.seat_no,
            external_id=attempt.external_id,
            message=message,
            failure_reason=attempt.failure_reason,
        )

    # --- 内部 ------------------------------------------------------

    def _already_reserved(
        self,
        item: RecurringReservation,
        target_date: date,
    ) -> Reservation | None:
        reservations = reservation_repo.list_reservations(
            self._db_path, since=target_date
        )
        for r in reservations:
            if (
                r.lesson_date == target_date
                and r.program_id == item.program_id
                and r.studio_id == item.studio_id
                and r.studio_room_id == item.studio_room_id
                and r.status is ReservationStatus.CONFIRMED
            ):
                return r
        return None

    def _resolve_target_lesson(
        self,
        item: RecurringReservation,
        target_date: date,
    ) -> Lesson | None:
        week_start = target_date - timedelta(days=target_date.weekday())
        try:
            lessons = self._gateway.fetch_week(
                StudioRef(item.studio_id, item.studio_room_id),
                week_start,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("failed to fetch schedule for recurring run: %s", exc)
            return None
        return self._match_lesson(lessons, lesson_date=target_date, item=item)

    def _match_lesson(
        self,
        lessons: list[Lesson],
        *,
        lesson_date: date,
        item: RecurringReservation,
    ) -> Lesson | None:
        same_day = [lsn for lsn in lessons if lsn.lesson_date == lesson_date]
        # 完全一致（時刻 + program_id）を最優先
        for lsn in same_day:
            if lsn.start_time == item.start_time and lsn.program_id == item.program_id:
                return lsn
        # program_id だけ一致（時間変更のケース）
        for lsn in same_day:
            if lsn.program_id == item.program_id:
                return lsn
        # program_id が変わった場合などは諦める
        return None

    def _record_history(
        self,
        item: RecurringReservation,
        *,
        target_date: date,
        result: HistoryResult,
        message: str,
        attempted_seats: list[int],
        seat_no: int | None,
        failure_reason: str | None,
        category: HistoryCategory = HistoryCategory.AUTOMATION,
        elapsed_ms: int | None = None,
        studio_lesson_id: int | None = None,
    ) -> None:
        history_repo.insert(
            self._db_path,
            HistoryEntry(
                id=None,
                request_id=uuid.uuid4().hex[:12],
                occurred_at=datetime.now(),
                category=category,
                endpoint="reservation.reserve",
                elapsed_ms=elapsed_ms,
                result=result,
                message=message,
                metadata={
                    "recurring_id": item.id,
                    "program_id": item.program_id,
                    "program_name": item.program_name,
                    "lesson_date": target_date.isoformat(),
                    "lesson_time": item.start_time,
                    "seat_no": seat_no,
                    "attempted_seats": attempted_seats,
                    "origin": ReservationOrigin.RECURRING.value,
                    "studio_lesson_id": studio_lesson_id,
                    "failure_reason": failure_reason,
                },
            ),
        )
