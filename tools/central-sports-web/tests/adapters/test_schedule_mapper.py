from __future__ import annotations

from datetime import date

from app.adapters.schedule_mapper import (
    map_my_reservations,
    map_reservation_result,
    map_reserved_nos,
    map_weekly_schedule,
)


def test_map_weekly_schedule_minimal() -> None:
    payload = {
        "data": {
            "studio_lessons": {
                "items": [
                    {
                        "id": 1001,
                        "date": "2026-04-25",
                        "start_at": "10:00",
                        "end_at": "10:55",
                        "program_id": "GPW55",
                        "instructor_id": 7,
                        "studio_room_space_id": 3,
                        "remain_reservation": 12,
                        "is_reservable": True,
                    }
                ],
                "programs": [{"id": "GPW55", "name": "GroupPower 55"}],
                "instructors": [{"id": 7, "nick_name": "福田"}],
                "studio_room_spaces": [{"id": 3, "name": "35席", "space_num": 35}],
            }
        }
    }
    lessons = map_weekly_schedule(payload, studio_id=79, studio_room_id=177)
    assert len(lessons) == 1
    lesson = lessons[0]
    assert lesson.studio_lesson_id == 1001
    assert lesson.lesson_date == date(2026, 4, 25)
    assert lesson.start_time == "10:00"
    assert lesson.program_name == "GroupPower 55"
    assert lesson.instructor_name == "福田"
    assert lesson.capacity == 35
    assert lesson.remaining_seats == 12
    assert lesson.is_reservable


def test_map_weekly_schedule_handles_missing_data() -> None:
    assert map_weekly_schedule({}, studio_id=79, studio_room_id=177) == []
    assert map_weekly_schedule({"data": {}}, studio_id=79, studio_room_id=177) == []


def test_map_weekly_schedule_handles_iso_datetime_start() -> None:
    payload = {
        "data": {
            "studio_lessons": {
                "items": [
                    {
                        "id": 1,
                        "date": "2026-04-25",
                        "start_at": "2026-04-25T10:30:00+09:00",
                        "program_id": "X",
                        "remain_reservation": 5,
                        "is_reservable": True,
                    }
                ],
            }
        }
    }
    lessons = map_weekly_schedule(payload, studio_id=79, studio_room_id=177)
    assert lessons[0].start_time == "10:30"


def test_map_my_reservations() -> None:
    payload = {
        "data": {
            "items": [
                {
                    "id": 123456,
                    "studio_lesson_id": 1001,
                    "date": "2026-04-25",
                    "start_at": "10:00",
                    "no": 21,
                    "studio_id": 79,
                    "studio_room_id": 177,
                    "program_id": "GPW55",
                    "program": {"name": "GroupPower 55"},
                    "status": "confirmed",
                }
            ]
        }
    }
    reservations = map_my_reservations(payload)
    assert len(reservations) == 1
    r = reservations[0]
    assert r.external_id == 123456
    assert r.seat_no == 21
    assert r.program_name == "GroupPower 55"


def test_map_reserved_nos_accepts_strings_and_ints() -> None:
    payload = {"data": {"nos": [1, 3, "5", 7]}}
    assert map_reserved_nos(payload) == [1, 3, 5, 7]


def test_map_reservation_result_ok() -> None:
    payload = {"data": {"reservation": {"id": 999, "no": 12}}}
    attempt = map_reservation_result(
        payload, studio_lesson_id=1001, attempted_preferences=[12, 21]
    )
    assert attempt.ok is True
    assert attempt.external_id == 999
    assert attempt.seat_no == 12
