"""Hacomono スケジュール API / 予約 API のレスポンスをドメインに変換する。

- `map_weekly_schedule`: `/api/master/studio-lessons/schedule` → list[Lesson]
- `map_my_reservations`: `/api/reservation/reservations` → list[Reservation]
- `map_reserved_nos`: `/api/reservation/reservations/{id}/no` → list[int]
- `map_reservation_result`: `/api/reservation/reservations/reserve` 成功応答 → `ReservationAttempt`

start_at / end_at は `HH:MM` の場合と ISO datetime の場合のどちらも許容する。
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Iterable
from datetime import date, datetime
from typing import Any

from app.domain.entities import (
    Lesson,
    LessonState,
    Reservation,
    ReservationAttempt,
    ReservationOrigin,
    ReservationStatus,
    SeatPosition,
)

logger = logging.getLogger(__name__)


_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})")


def _parse_time(value: Any) -> str | None:
    """`"10:00"`、`"10:00:00"`、`"2026-04-25T10:00:00+09:00"` を HH:MM に揃える。"""

    if value is None:
        return None
    text = str(value)
    if "T" in text or " " in text:
        # ISO datetime の場合
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return f"{dt.hour:02d}:{dt.minute:02d}"
        except ValueError:
            pass
        m = _TIME_RE.search(text.split("T")[-1] if "T" in text else text.split(" ")[-1])
        if m:
            return f"{int(m.group(1)):02d}:{m.group(2)}"
        return None
    m = _TIME_RE.match(text)
    if m:
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    return None


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    text = str(value)
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _index_by(items: Iterable[dict], key: str) -> dict[Any, dict]:
    out: dict[Any, dict] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        k = item.get(key)
        if k is not None:
            out[k] = item
    return out


# ---------------------------------------------------------------
# スケジュール
# ---------------------------------------------------------------
def map_weekly_schedule(
    payload: dict,
    *,
    studio_id: int,
    studio_room_id: int,
) -> list[Lesson]:
    """schedule API のレスポンスを `Lesson` のリストに変換する。

    `studio_lessons.programs` / `instructors` / `studio_room_spaces` から
    名称と定員を解決して紐付ける。
    """

    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return []
    studio_lessons = data.get("studio_lessons")
    if not isinstance(studio_lessons, dict):
        return []

    items = studio_lessons.get("items") or []
    programs = _index_by(studio_lessons.get("programs") or [], "id")
    instructors = _index_by(studio_lessons.get("instructors") or [], "id")
    spaces = _index_by(studio_lessons.get("studio_room_spaces") or [], "id")

    lessons: list[Lesson] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        lesson_date = _parse_date(item.get("date"))
        if lesson_date is None:
            continue
        start_time = _parse_time(item.get("start_at"))
        if start_time is None:
            continue
        end_time = _parse_time(item.get("end_at"))
        program_id_raw = item.get("program_id")
        program_id = str(program_id_raw) if program_id_raw is not None else ""
        program_entry = programs.get(program_id_raw) or {}
        program_name = str(program_entry.get("name") or program_id or "レッスン")
        # キッズ等のスクール系は除外（大人の単発予約対象外）
        if "スクール" in program_name:
            continue

        instructor_id = _coerce_int(item.get("instructor_id"))
        instructor_entry = instructors.get(instructor_id) or instructors.get(item.get("instructor_id")) or {}
        instructor_name = (
            instructor_entry.get("nick_name") or instructor_entry.get("name")
            if instructor_entry
            else None
        )

        space_id = _coerce_int(item.get("studio_room_space_id"))
        space_entry = spaces.get(space_id) or {}
        capacity = _coerce_int(space_entry.get("space_num")) or _coerce_int(item.get("capacity"))
        layout_name = space_entry.get("name") if space_entry else None

        # hacomono の item には `remain_reservation` フィールドは無い。
        # 満席判定は capacity − reservation_count（通常） − reservation_trial_count（体験）で算出する。
        reservation_count = _coerce_int(item.get("reservation_count")) or 0
        trial_count = _coerce_int(item.get("reservation_trial_count")) or 0
        reserved_total = reservation_count + trial_count
        remaining: int | None
        if capacity is not None:
            remaining = max(0, capacity - reserved_total)
        else:
            remaining = _coerce_int(item.get("remain_reservation"))
        is_reservable = bool(item.get("is_reservable"))

        state = LessonState.AVAILABLE
        if remaining is not None and remaining <= 0:
            state = LessonState.FULL
        elif not is_reservable:
            state = LessonState.UNRESERVABLE

        lesson = Lesson(
            studio_lesson_id=_coerce_int(item.get("id")) or 0,
            studio_id=studio_id,
            studio_room_id=studio_room_id,
            lesson_date=lesson_date,
            start_time=start_time,
            end_time=end_time,
            program_id=program_id,
            program_name=program_name,
            instructor_id=instructor_id,
            instructor_name=instructor_name,
            capacity=capacity,
            remaining_seats=remaining,
            studio_room_space_id=space_id,
            space_layout_name=layout_name,
            is_reservable=is_reservable,
            reservable_from=_parse_datetime(item.get("reservable_from")),
            reservable_to=_parse_datetime(item.get("reservable_to")),
            state=state,
        )
        lessons.append(lesson)

    # hacomono schedule API は同一レッスンを複数 item で返すケースがある
    # （通常枠 + 体験枠 等）。(date, time, program_id, space_id) キーで dedupe し、
    # 最初に現れた 1 件のみを残す。UI での重複表示と、予約ロジックでの誤マッチを防ぐ。
    seen: set[tuple] = set()
    deduped: list[Lesson] = []
    for lsn in lessons:
        key = (
            lsn.lesson_date,
            lsn.start_time,
            lsn.program_id,
            lsn.studio_room_space_id,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(lsn)
    lessons = deduped

    lessons.sort(key=lambda lsn: (lsn.lesson_date, lsn.start_time))
    return lessons


# ---------------------------------------------------------------
# studio_room_space の space_details（座席配置）
# ---------------------------------------------------------------
def map_space_details(space_entry: dict) -> list[SeatPosition]:
    """`studio_room_spaces[i].space_details` を `SeatPosition` のリストに変換する。

    各 detail には `no`（予約 API に送る内部連番）、`no_label`（UI 表示）、
    `coord_x` / `coord_y`（グリッド座標）が入っている。数値化できない要素は skip。
    """

    if not isinstance(space_entry, dict):
        return []
    details = space_entry.get("space_details") or []
    positions: list[SeatPosition] = []
    for d in details:
        if not isinstance(d, dict):
            continue
        no = _coerce_int(d.get("no"))
        cx = _coerce_int(d.get("coord_x"))
        cy = _coerce_int(d.get("coord_y"))
        if no is None or cx is None or cy is None:
            continue
        label_raw = d.get("no_label")
        label = str(label_raw) if label_raw is not None else str(no)
        positions.append(
            SeatPosition(no=no, no_label=label, coord_x=cx, coord_y=cy)
        )
    positions.sort(key=lambda p: p.no)
    return positions


def build_space_index(payload: dict) -> dict[int, list[SeatPosition]]:
    """schedule API の payload から `studio_room_space_id → positions` 辞書を作る。"""

    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return {}
    studio_lessons = data.get("studio_lessons")
    if not isinstance(studio_lessons, dict):
        return {}
    spaces = studio_lessons.get("studio_room_spaces") or []
    index: dict[int, list[SeatPosition]] = {}
    for entry in spaces:
        if not isinstance(entry, dict):
            continue
        space_id = _coerce_int(entry.get("id"))
        if space_id is None:
            continue
        index[space_id] = map_space_details(entry)
    return index


# ---------------------------------------------------------------
# 自分の予約一覧
# ---------------------------------------------------------------
def map_my_reservations(payload: dict) -> list[Reservation]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return []
    # hacomono のレスポンスは data.reservations が dict（page/list 付き）で返る形と、
    # リスト直値の形が混在する可能性があるため両対応。
    items: list = []
    reservations_field = data.get("reservations")
    if isinstance(reservations_field, dict):
        items = reservations_field.get("list") or reservations_field.get("items") or []
    elif isinstance(reservations_field, list):
        items = reservations_field
    if not items:
        items = data.get("items") or []
    results: list[Reservation] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        lesson_date = _parse_date(item.get("date"))
        if lesson_date is None:
            continue
        start_time = _parse_time(item.get("start_at")) or "00:00"
        program = item.get("program") if isinstance(item.get("program"), dict) else {}
        studio_obj = item.get("studio") if isinstance(item.get("studio"), dict) else {}

        program_id_raw = item.get("program_id")
        program_id = str(program_id_raw) if program_id_raw is not None else ""
        program_name = str(program.get("name") or item.get("program_name") or program_id or "レッスン")
        # 予約一覧 API は instructor（単数辞書）ではなく instructors（配列）で返す。
        # 先頭の instructor を代表として採用（ほぼ常に 1 人）。
        instructor = None
        instructor_list = item.get("instructors")
        if isinstance(instructor_list, list):
            for entry in instructor_list:
                if isinstance(entry, dict):
                    instructor = entry.get("nick_name") or entry.get("name")
                    if instructor:
                        break
        if instructor is None and isinstance(item.get("instructor"), dict):
            instructor = item["instructor"].get("nick_name") or item["instructor"].get("name")

        studio_id = _coerce_int(item.get("studio_id") or studio_obj.get("id")) or 0
        studio_room_id = _coerce_int(item.get("studio_room_id")) or 0
        external_id = _coerce_int(item.get("id"))
        studio_lesson_id = _coerce_int(item.get("studio_lesson_id")) or 0
        seat_no = _coerce_int(item.get("no"))
        status_str = str(item.get("status") or "confirmed").lower()
        try:
            status = ReservationStatus(status_str)
        except ValueError:
            status = ReservationStatus.CONFIRMED

        results.append(
            Reservation(
                id=f"ext-{external_id}" if external_id is not None else uuid.uuid4().hex,
                studio_lesson_id=studio_lesson_id,
                lesson_date=lesson_date,
                lesson_time=start_time,
                program_id=program_id,
                program_name=program_name,
                studio_id=studio_id,
                studio_room_id=studio_room_id,
                external_id=external_id,
                instructor_name=instructor,
                seat_no=seat_no,
                origin=ReservationOrigin.SINGLE,  # 同期時点では由来不明、後で上書き
                status=status,
            )
        )
    return results


# ---------------------------------------------------------------
# 予約済み席
# ---------------------------------------------------------------
def map_reserved_nos(payload: dict) -> list[int]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return []
    nos = data.get("nos") or []
    result: list[int] = []
    for n in nos:
        coerced = _coerce_int(n)
        if coerced is not None:
            result.append(coerced)
    return result


# ---------------------------------------------------------------
# 予約結果
# ---------------------------------------------------------------
def map_reservation_result(
    payload: dict,
    *,
    studio_lesson_id: int,
    attempted_preferences: list[int],
) -> ReservationAttempt:
    data = payload.get("data") if isinstance(payload, dict) else None
    reservation = None
    if isinstance(data, dict):
        reservation = data.get("reservation") if isinstance(data.get("reservation"), dict) else None
    seat_no = _coerce_int(reservation.get("no")) if reservation else None
    external_id = _coerce_int(reservation.get("id")) if reservation else None
    if reservation and seat_no is not None and external_id is not None:
        return ReservationAttempt(
            ok=True,
            studio_lesson_id=studio_lesson_id,
            attempted_preferences=list(attempted_preferences),
            seat_no=seat_no,
            external_id=external_id,
            message="予約成功",
        )
    # ここに来るケースは通常例外側で拾われるため、ガード的な扱い
    return ReservationAttempt(
        ok=False,
        studio_lesson_id=studio_lesson_id,
        attempted_preferences=list(attempted_preferences),
        message="予約応答から結果を解釈できませんでした",
        failure_reason="ProtocolViolation",
    )
