"""予約予定（先の週のレッスンを開放日に自動予約）のユースケース。

- 登録: 公開月間で見えた未開放レッスンに対して、希望席と予約実行日時で登録
- 実行: 9:00 ジョブが pending な intent のうち scheduled_run_at <= today を拾い、
  当該週のスケジュールを取得して studio_lesson_id を解決 → 予約 POST
- 冪等性: 同日・同 program_id・同店舗の CONFIRMED 予約があれば skip
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

from app.domain.entities import (
    BookingIntent,
    HistoryCategory,
    HistoryEntry,
    HistoryResult,
    IntentStatus,
    Lesson,
    Reservation,
    ReservationOrigin,
    ReservationStatus,
    StudioRef,
)
from app.domain.errors import NotFound
from app.domain.ports import ReservationGateway
from config.settings import Settings
from db.repositories import history_repo, intent_repo, reservation_repo

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class IntentRunResult:
    intent_id: str
    ok: bool
    seat_no: int | None
    external_id: int | None
    message: str
    failure_reason: str | None = None


class BookingIntentService:
    """先の予約を予約予定として保存し、開放日に実行する。"""

    def __init__(
        self,
        db_path: Path,
        gateway: ReservationGateway,
        settings: Settings,
    ) -> None:
        self._db_path = db_path
        self._gateway = gateway
        self._settings = settings

    def compute_run_at(self, lesson_date: date) -> datetime:
        """予約開放は対象日の 6 日前 9:00。

        hacomono の `schedule_open_days=7` は「今日含む 7 日間」を公開する仕様なので、
        9:00 に新規解放されるのは `today + 6` の日付。逆算すると、対象レッスンの
        予約実行タイミングは `lesson_date - 6 日` の 9:00。
        例: 4/25 (土) のレッスン → 4/19 (日) 09:00 に実行。
        """

        return datetime.combine(
            lesson_date - timedelta(days=6),
            time(self._settings.run_hour, self._settings.run_minute),
        )

    def create(
        self,
        *,
        lesson_date: date,
        lesson_time: str,
        program_id: str,
        program_name: str,
        studio_id: int,
        studio_room_id: int,
        seat_preferences: list[int],
        note: str | None = None,
    ) -> BookingIntent:
        run_at = self.compute_run_at(lesson_date)
        intent = BookingIntent(
            id=uuid.uuid4().hex,
            lesson_date=lesson_date,
            lesson_time=lesson_time,
            program_id=program_id,
            program_name=program_name,
            studio_id=studio_id,
            studio_room_id=studio_room_id,
            seat_preferences=seat_preferences,
            status=IntentStatus.PENDING,
            scheduled_run_at=run_at,
            note=note,
        )
        intent_repo.insert_intent(self._db_path, intent)
        return intent

    def list_all(
        self,
        *,
        status: IntentStatus | None = None,
        pending_from: date | None = None,
    ) -> list[BookingIntent]:
        return intent_repo.list_intents(
            self._db_path, status=status, pending_from=pending_from
        )

    def list_pending_upcoming(self, *, from_date: date) -> list[BookingIntent]:
        return [
            i
            for i in intent_repo.list_intents(
                self._db_path, status=IntentStatus.PENDING, pending_from=from_date
            )
            if i.lesson_date >= from_date
        ]

    def cancel(self, intent_id: str) -> None:
        intent = intent_repo.get_intent(self._db_path, intent_id)
        if intent is None:
            raise NotFound(f"intent {intent_id} not found")
        intent_repo.update_status(self._db_path, intent_id, IntentStatus.CANCELLED)

    def update_seats(self, intent_id: str, seat_preferences: list[int]) -> BookingIntent:
        """登録済み intent の希望席だけを更新する（取り消し→再登録を不要にする）。"""

        intent = intent_repo.get_intent(self._db_path, intent_id)
        if intent is None:
            raise NotFound(f"intent {intent_id} not found")
        if intent.status is not IntentStatus.PENDING:
            raise NotFound(f"intent {intent_id} is not editable (status={intent.status.value})")
        intent_repo.update_seat_preferences(self._db_path, intent_id, seat_preferences)
        intent.seat_preferences = list(seat_preferences)
        return intent

    def execute_due(
        self,
        *,
        today: date,
        target_date: date | None = None,
    ) -> list[IntentRunResult]:
        """本日までに開放された（予約すべき）intent をすべて実行。

        ``target_date`` が指定された場合は ``intent.lesson_date`` が一致するものだけ
        実行する。9:00 ジョブから呼ぶ際に「その日新規解放された日」だけに絞るための
        フィルタ。未指定時は従来通り全件実行する（互換性維持）。
        """

        runnable = intent_repo.list_runnable_on(self._db_path, today)
        if target_date is not None:
            runnable = [i for i in runnable if i.lesson_date == target_date]
        results: list[IntentRunResult] = []
        for intent in runnable:
            results.append(self._execute_one(intent))
        return results

    # --- 内部 -----------------------------------------------------

    def _execute_one(self, intent: BookingIntent) -> IntentRunResult:
        if self._already_reserved(intent):
            intent_repo.update_status(
                self._db_path, intent.id, IntentStatus.EXECUTED, executed_at=datetime.now()
            )
            return IntentRunResult(
                intent_id=intent.id,
                ok=True,
                seat_no=None,
                external_id=None,
                message="既に同日同プログラムの予約があるためスキップ（二重予約防止）",
            )

        lesson = self._resolve_lesson(intent)
        if lesson is None:
            message = "対象レッスンを見つけられませんでした（レイアウトまたは時刻変更の可能性）"
            self._record_history(
                intent,
                result=HistoryResult.FAILURE,
                message=message,
                seat_no=None,
                failure_reason="LessonNotFound",
            )
            intent_repo.update_status(
                self._db_path, intent.id, IntentStatus.FAILED, executed_at=datetime.now()
            )
            return IntentRunResult(
                intent_id=intent.id,
                ok=False,
                seat_no=None,
                external_id=None,
                message=message,
                failure_reason="LessonNotFound",
            )

        started = datetime.now()
        attempt = self._gateway.attempt_reservation(
            studio_lesson_id=lesson.studio_lesson_id,
            no_preferences=intent.seat_preferences,
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
                origin=ReservationOrigin.INTENT,
                origin_id=intent.id,
                status=ReservationStatus.CONFIRMED,
            )
            reservation_repo.upsert_reservation(self._db_path, reservation)
            intent_repo.update_status(
                self._db_path, intent.id, IntentStatus.EXECUTED, executed_at=datetime.now()
            )
            result = (
                HistoryResult.SUCCESS
                if attempt.succeeded_with_first_choice
                else HistoryResult.WARNING
            )
            message = attempt.message or (
                f"{lesson.program_name} を席 {attempt.seat_no:02d} で予約しました"
            )
        else:
            intent_repo.update_status(
                self._db_path, intent.id, IntentStatus.FAILED, executed_at=datetime.now()
            )
            result = HistoryResult.FAILURE
            message = attempt.message or "予約に失敗しました"

        self._record_history(
            intent,
            result=result,
            message=message,
            seat_no=attempt.seat_no,
            failure_reason=attempt.failure_reason,
            elapsed_ms=elapsed_ms,
            studio_lesson_id=lesson.studio_lesson_id,
        )
        return IntentRunResult(
            intent_id=intent.id,
            ok=attempt.ok,
            seat_no=attempt.seat_no,
            external_id=attempt.external_id,
            message=message,
            failure_reason=attempt.failure_reason,
        )

    def _already_reserved(self, intent: BookingIntent) -> bool:
        reservations = reservation_repo.list_reservations(
            self._db_path, since=intent.lesson_date
        )
        for r in reservations:
            if (
                r.lesson_date == intent.lesson_date
                and r.program_id == intent.program_id
                and r.studio_id == intent.studio_id
                and r.studio_room_id == intent.studio_room_id
                and r.status is ReservationStatus.CONFIRMED
            ):
                return True
        return False

    def _resolve_lesson(self, intent: BookingIntent) -> Lesson | None:
        week_start = intent.lesson_date - timedelta(days=intent.lesson_date.weekday())
        try:
            lessons = self._gateway.fetch_week(
                StudioRef(intent.studio_id, intent.studio_room_id),
                week_start,
                days=7,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("intent: fetch_week failed: %s", exc)
            return None
        same_day = [lsn for lsn in lessons if lsn.lesson_date == intent.lesson_date]
        # 時刻＋program_id 完全一致を優先、次に program_id のみ
        for lsn in same_day:
            if lsn.program_id == intent.program_id and lsn.start_time == intent.lesson_time:
                return lsn
        for lsn in same_day:
            if lsn.program_id == intent.program_id:
                return lsn
        return None

    def _record_history(
        self,
        intent: BookingIntent,
        *,
        result: HistoryResult,
        message: str,
        seat_no: int | None,
        failure_reason: str | None,
        elapsed_ms: int | None = None,
        studio_lesson_id: int | None = None,
    ) -> None:
        history_repo.insert(
            self._db_path,
            HistoryEntry(
                id=None,
                request_id=uuid.uuid4().hex[:12],
                occurred_at=datetime.now(),
                category=HistoryCategory.AUTOMATION,
                endpoint="intent.reserve",
                elapsed_ms=elapsed_ms,
                result=result,
                message=message,
                metadata={
                    "intent_id": intent.id,
                    "program_id": intent.program_id,
                    "program_name": intent.program_name,
                    "lesson_date": intent.lesson_date.isoformat(),
                    "lesson_time": intent.lesson_time,
                    "seat_no": seat_no,
                    "attempted_seats": intent.seat_preferences,
                    "origin": ReservationOrigin.INTENT.value,
                    "studio_lesson_id": studio_lesson_id,
                    "failure_reason": failure_reason,
                },
            ),
        )
