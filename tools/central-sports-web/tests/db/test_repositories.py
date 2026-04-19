from __future__ import annotations

from datetime import date
from pathlib import Path

from app.domain.entities import (
    RecurringReservation,
    RecurringStatus,
    Reservation,
    ReservationOrigin,
    ReservationStatus,
)
from db.migrations import run_migrations
from db.repositories import recurring_repo, reservation_repo, studio_repo


def _prepare_db(tmp_path: Path) -> Path:
    db = tmp_path / "app.db"
    run_migrations(db)
    return db


def test_default_studio_seeded(tmp_path: Path) -> None:
    db = _prepare_db(tmp_path)
    studio = studio_repo.get_default_studio(db)
    assert studio is not None
    assert studio.studio_id == 79
    assert studio.studio_room_id == 177


def test_recurring_crud(tmp_path: Path) -> None:
    db = _prepare_db(tmp_path)
    item = RecurringReservation(
        id="abc",
        day_of_week=1,
        start_time="10:00",
        program_id="GPW55",
        program_name="GroupPower 55",
        studio_id=79,
        studio_room_id=177,
        seat_preferences=[21, 15, 8],
        status=RecurringStatus.ACTIVE,
    )
    recurring_repo.insert_recurring(db, item)
    got = recurring_repo.get_recurring(db, "abc")
    assert got is not None
    assert got.seat_preferences == [21, 15, 8]
    assert got.status is RecurringStatus.ACTIVE

    recurring_repo.update_status(db, "abc", RecurringStatus.PAUSED)
    paused = recurring_repo.get_recurring(db, "abc")
    assert paused is not None
    assert paused.status is RecurringStatus.PAUSED


def test_reservation_upsert_and_cancel(tmp_path: Path) -> None:
    db = _prepare_db(tmp_path)
    r = Reservation(
        id="r1",
        external_id=1000,
        studio_lesson_id=1,
        lesson_date=date(2026, 4, 25),
        lesson_time="10:00",
        program_id="X",
        program_name="Xrg",
        studio_id=79,
        studio_room_id=177,
        seat_no=21,
        origin=ReservationOrigin.SINGLE,
        status=ReservationStatus.CONFIRMED,
    )
    reservation_repo.upsert_reservation(db, r)
    rows = reservation_repo.list_reservations(db)
    assert len(rows) == 1
    assert rows[0].external_id == 1000

    # external_id が消えた想定でキャンセル扱いに
    reservation_repo.mark_missing_as_cancelled(db, active_external_ids=[])
    rows_after = reservation_repo.list_reservations(db, status=ReservationStatus.CANCELLED)
    assert len(rows_after) == 1
