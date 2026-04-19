"""単発予約のユースケース。

画面からの予約作成・取消・席変更を受け、Gateway と DB に反映する。
dry-run ガードは `HacomonoGateway` 側で行い、本ユースケースはその結果を受けるだけ。
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime
from pathlib import Path

from app.domain.entities import (
    HistoryCategory,
    HistoryEntry,
    HistoryResult,
    Reservation,
    ReservationAttempt,
    ReservationOrigin,
    ReservationStatus,
)
from app.domain.errors import NotFound
from app.domain.ports import ReservationGateway
from db.repositories import history_repo, reservation_repo

logger = logging.getLogger(__name__)


class ReserveSingleService:
    def __init__(
        self,
        db_path: Path,
        gateway: ReservationGateway,
    ) -> None:
        self._db_path = db_path
        self._gateway = gateway

    def create(
        self,
        *,
        studio_lesson_id: int,
        lesson_date: date,
        lesson_time: str,
        program_id: str,
        program_name: str,
        instructor_name: str | None,
        studio_id: int,
        studio_room_id: int,
        seat_no: int,
    ) -> ReservationAttempt:
        started = datetime.now()
        attempt = self._gateway.attempt_reservation(
            studio_lesson_id=studio_lesson_id,
            no_preferences=[seat_no],
        )
        elapsed_ms = int((datetime.now() - started).total_seconds() * 1000)

        if attempt.ok and attempt.external_id is not None:
            reservation = Reservation(
                id=uuid.uuid4().hex,
                external_id=attempt.external_id,
                studio_lesson_id=studio_lesson_id,
                lesson_date=lesson_date,
                lesson_time=lesson_time,
                program_id=program_id,
                program_name=program_name,
                instructor_name=instructor_name,
                studio_id=studio_id,
                studio_room_id=studio_room_id,
                seat_no=attempt.seat_no,
                origin=ReservationOrigin.SINGLE,
                status=ReservationStatus.CONFIRMED,
            )
            reservation_repo.upsert_reservation(self._db_path, reservation)

        history_repo.insert(
            self._db_path,
            HistoryEntry(
                id=None,
                request_id=uuid.uuid4().hex[:12],
                occurred_at=datetime.now(),
                category=HistoryCategory.MANUAL,
                endpoint="reservation.create",
                elapsed_ms=elapsed_ms,
                result=HistoryResult.SUCCESS if attempt.ok else HistoryResult.FAILURE,
                message=attempt.message
                or ("予約しました" if attempt.ok else "予約できませんでした"),
                metadata={
                    "program_id": program_id,
                    "program_name": program_name,
                    "lesson_date": lesson_date.isoformat(),
                    "lesson_time": lesson_time,
                    "seat_no": attempt.seat_no,
                    "origin": ReservationOrigin.SINGLE.value,
                    "studio_lesson_id": studio_lesson_id,
                    "failure_reason": attempt.failure_reason,
                },
            ),
        )
        return attempt

    def cancel(self, reservation_id: str) -> Reservation:
        reservation = reservation_repo.get_by_id(self._db_path, reservation_id)
        if reservation is None:
            raise NotFound(f"reservation {reservation_id} not found")
        if reservation.external_id is not None:
            self._gateway.cancel_reservation(reservation.external_id)
        reservation_repo.update_status(
            self._db_path, reservation_id, ReservationStatus.CANCELLED
        )
        history_repo.insert(
            self._db_path,
            HistoryEntry(
                id=None,
                request_id=uuid.uuid4().hex[:12],
                occurred_at=datetime.now(),
                category=HistoryCategory.MANUAL,
                endpoint="reservation.cancel",
                elapsed_ms=None,
                result=HistoryResult.SUCCESS,
                message=f"{reservation.program_name} の予約を取り消しました",
                metadata={
                    "program_id": reservation.program_id,
                    "program_name": reservation.program_name,
                    "lesson_date": reservation.lesson_date.isoformat(),
                    "lesson_time": reservation.lesson_time,
                    "seat_no": reservation.seat_no,
                },
            ),
        )
        return reservation

    def change_seat(
        self,
        reservation_id: str,
        new_seat_no: int,
    ) -> Reservation:
        reservation = reservation_repo.get_by_id(self._db_path, reservation_id)
        if reservation is None:
            raise NotFound(f"reservation {reservation_id} not found")
        if reservation.external_id is not None:
            self._gateway.change_seat(reservation.external_id, new_seat_no)
        reservation_repo.update_seat(self._db_path, reservation_id, new_seat_no)
        history_repo.insert(
            self._db_path,
            HistoryEntry(
                id=None,
                request_id=uuid.uuid4().hex[:12],
                occurred_at=datetime.now(),
                category=HistoryCategory.MANUAL,
                endpoint="reservation.move",
                elapsed_ms=None,
                result=HistoryResult.SUCCESS,
                message=f"{reservation.program_name} の席を {new_seat_no:02d} に変更しました",
                metadata={
                    "program_id": reservation.program_id,
                    "program_name": reservation.program_name,
                    "lesson_date": reservation.lesson_date.isoformat(),
                    "lesson_time": reservation.lesson_time,
                    "seat_no": new_seat_no,
                },
            ),
        )
        reservation.seat_no = new_seat_no
        return reservation
