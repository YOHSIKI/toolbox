"""仮スケジュール生成ロジックのテスト。

`CalendarQueryService._build_tentative_lessons` と
`observed_lesson_repo.list_by_date` を中心に検証する。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from app.domain.entities import Lesson, LessonState, Studio, StudioRef
from app.services.calendar_query import CalendarQueryService
from db.migrations import run_migrations
from db.repositories import observed_lesson_repo


class _NullGateway:
    """Protocol 充足用のダミー gateway。`_build_tentative_lessons` の単体テストでは
    実際には呼ばれないので中身は empty で問題ない。"""

    def ensure_authenticated(self) -> None:
        return None

    def fetch_week(self, studio, week_start, days=7):  # type: ignore[no-untyped-def]
        return []

    def fetch_my_reservations(self):  # type: ignore[no-untyped-def]
        return []

    def fetch_seat_map(self, studio_lesson_id, *, capacity_hint=None, studio_room_space_id=None):  # type: ignore[no-untyped-def]
        raise AssertionError("fetch_seat_map should not be called in tentative tests")

    def attempt_reservation(self, studio_lesson_id, no_preferences):  # type: ignore[no-untyped-def]
        raise AssertionError("attempt_reservation should not be called")

    def cancel_reservation(self, external_id):  # type: ignore[no-untyped-def]
        raise AssertionError("cancel_reservation should not be called")

    def change_seat(self, external_id, new_no):  # type: ignore[no-untyped-def]
        raise AssertionError("change_seat should not be called")


STUDIO = Studio(
    id=1,
    studio_id=79,
    studio_room_id=177,
    display_name="府中",
    club_code="054",
    sisetcd="A1",
    is_default=True,
)


def _prepare_db(tmp_path: Path) -> Path:
    db = tmp_path / "app.db"
    run_migrations(db)
    return db


def _observed(lesson_date: date, start_time: str, program_id: str, program_name: str) -> Lesson:
    """observed_lessons に流し込む Lesson（reserve API 由来を模擬）。"""

    return Lesson(
        studio_lesson_id=1234,  # != 0 でないと upsert_many でスキップされる
        studio_id=STUDIO.studio_id,
        studio_room_id=STUDIO.studio_room_id,
        lesson_date=lesson_date,
        start_time=start_time,
        end_time=None,
        program_id=program_id,
        program_name=program_name,
        instructor_id=None,
        instructor_name="森",
        capacity=35,
        remaining_seats=10,
        studio_room_space_id=42,
        space_layout_name=None,
        is_reservable=True,
        reservable_from=None,
        reservable_to=None,
        state=LessonState.AVAILABLE,
    )


def _service(db: Path) -> CalendarQueryService:
    return CalendarQueryService(db_path=db, gateway=_NullGateway())  # type: ignore[arg-type]


def test_list_by_date_returns_observed_rows_sorted(tmp_path: Path) -> None:
    db = _prepare_db(tmp_path)
    observed_lesson_repo.upsert_many(
        db,
        [
            _observed(date(2026, 4, 22), "19:30", "P1", "ZUMBA"),
            _observed(date(2026, 4, 22), "10:00", "P2", "GroupPower"),
            _observed(date(2026, 4, 15), "10:00", "P2", "GroupPower"),
        ],
    )

    rows = observed_lesson_repo.list_by_date(
        db,
        studio_id=STUDIO.studio_id,
        studio_room_id=STUDIO.studio_room_id,
        lesson_date=date(2026, 4, 22),
    )
    assert [r["start_time"] for r in rows] == ["10:00", "19:30"]
    assert rows[0]["program_name"] == "GroupPower"
    assert rows[1]["program_name"] == "ZUMBA"


def test_build_tentative_uses_one_week_back_when_available(tmp_path: Path) -> None:
    db = _prepare_db(tmp_path)
    # target=2026-04-29 (祝日)、1週前=2026-04-22 に観測あり
    observed_lesson_repo.upsert_many(
        db,
        [
            _observed(date(2026, 4, 22), "10:00", "P1", "GroupPower"),
            _observed(date(2026, 4, 22), "19:30", "P2", "ZUMBA"),
        ],
    )
    service = _service(db)

    result = service._build_tentative_lessons(
        STUDIO,
        target_dates={date(2026, 4, 29)},
        today=date(2026, 4, 24),
    )

    assert len(result) == 2
    assert all(lesson.lesson_date == date(2026, 4, 29) for lesson in result)
    assert all(lesson.tentative_source == date(2026, 4, 22) for lesson in result)
    assert all(lesson.release_pending is True for lesson in result)
    assert all(lesson.studio_lesson_id == 0 for lesson in result)
    assert all(lesson.is_reservable is False for lesson in result)
    times = sorted(lesson.start_time for lesson in result)
    assert times == ["10:00", "19:30"]


def test_build_tentative_falls_back_to_two_weeks_back(tmp_path: Path) -> None:
    db = _prepare_db(tmp_path)
    # 1週前 (04-22) は空、2週前 (04-15) にあり
    observed_lesson_repo.upsert_many(
        db,
        [_observed(date(2026, 4, 15), "10:00", "P1", "GroupPower")],
    )
    service = _service(db)

    result = service._build_tentative_lessons(
        STUDIO,
        target_dates={date(2026, 4, 29)},
        today=date(2026, 4, 24),
    )

    assert len(result) == 1
    assert result[0].tentative_source == date(2026, 4, 15)
    assert result[0].lesson_date == date(2026, 4, 29)


def test_build_tentative_returns_empty_when_no_history(tmp_path: Path) -> None:
    db = _prepare_db(tmp_path)
    # observed 無し
    service = _service(db)

    result = service._build_tentative_lessons(
        STUDIO,
        target_dates={date(2026, 4, 29)},
        today=date(2026, 4, 24),
    )
    assert result == []


def test_build_tentative_skips_past_dates(tmp_path: Path) -> None:
    db = _prepare_db(tmp_path)
    # 1週前に観測があっても、target が過去日なら出さない
    observed_lesson_repo.upsert_many(
        db,
        [_observed(date(2026, 4, 15), "10:00", "P1", "GroupPower")],
    )
    service = _service(db)

    result = service._build_tentative_lessons(
        STUDIO,
        target_dates={date(2026, 4, 22)},  # today より前
        today=date(2026, 4, 24),
    )
    assert result == []


def test_build_tentative_picks_newest_source_first(tmp_path: Path) -> None:
    """1週前・2週前の両方にデータがあれば、1週前を優先する。"""

    db = _prepare_db(tmp_path)
    observed_lesson_repo.upsert_many(
        db,
        [
            _observed(date(2026, 4, 15), "10:00", "P_OLD", "Old Program"),
            _observed(date(2026, 4, 22), "10:00", "P_NEW", "New Program"),
        ],
    )
    service = _service(db)

    result = service._build_tentative_lessons(
        STUDIO,
        target_dates={date(2026, 4, 29)},
        today=date(2026, 4, 24),
    )

    assert len(result) == 1
    assert result[0].tentative_source == date(2026, 4, 22)
    assert result[0].program_name == "New Program"


def test_studio_ref_matches_expected(tmp_path: Path) -> None:
    """ID・部屋の組がずれると別店舗のデータを拾わないことを最小限確認する。"""

    db = _prepare_db(tmp_path)
    observed_lesson_repo.upsert_many(
        db,
        [_observed(date(2026, 4, 22), "10:00", "P", "GP")],
    )
    # StudioRef の tuple を一応参照
    assert STUDIO.ref == StudioRef(79, 177)

    other = Studio(
        id=2,
        studio_id=12345,  # 違う店舗
        studio_room_id=99,
        display_name="別店舗",
        club_code=None,
        sisetcd=None,
        is_default=False,
    )
    service = _service(db)
    result = service._build_tentative_lessons(
        other,
        target_dates={date(2026, 4, 29)},
        today=date(2026, 4, 24),
    )
    assert result == []
