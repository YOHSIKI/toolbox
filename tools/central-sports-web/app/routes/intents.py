"""予約予定（Booking Intent）の登録・削除。"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.deps import AppContext, get_context
from app.domain.errors import NotFound
from app.routes._shared import resolve_current_studio

router = APIRouter()


def _parse_seat_preferences(raw: str) -> list[int]:
    raw = (raw or "").replace("、", ",")
    seats: list[int] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            seats.append(int(token))
        except ValueError:
            continue
    seen: set[int] = set()
    deduped: list[int] = []
    for s in seats:
        if s in seen:
            continue
        seen.add(s)
        deduped.append(s)
    return deduped


@router.post("/reserve/intent", name="intent_create")
def intent_create(
    request: Request,
    lesson_date: str = Form(...),
    lesson_time: str = Form(...),
    program_id: str = Form(...),
    program_name: str = Form(...),
    seat_preferences: str = Form(""),
    return_to: str = Form("/reserve"),
    context: AppContext = Depends(get_context),
) -> RedirectResponse:
    if context.booking_intent is None:
        raise HTTPException(503, "認証情報が未設定のため予約予定を登録できません")
    studio = resolve_current_studio(request, context.db_path)
    if studio is None:
        raise HTTPException(500, "店舗が登録されていません")
    try:
        lesson_date_value = date.fromisoformat(lesson_date)
    except ValueError as exc:
        raise HTTPException(400, "日付の形式が不正です") from exc
    seats = _parse_seat_preferences(seat_preferences)
    context.booking_intent.create(
        lesson_date=lesson_date_value,
        lesson_time=lesson_time,
        program_id=program_id,
        program_name=program_name,
        studio_id=studio.studio_id,
        studio_room_id=studio.studio_room_id,
        seat_preferences=seats,
    )
    return RedirectResponse(url=return_to, status_code=303)


@router.post("/reserve/intent/{intent_id}/cancel", name="intent_cancel")
def intent_cancel(
    intent_id: str,
    return_to: str = Form("/"),
    context: AppContext = Depends(get_context),
) -> RedirectResponse:
    if context.booking_intent is None:
        raise HTTPException(503, "認証情報が未設定のため操作できません")
    try:
        context.booking_intent.cancel(intent_id)
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    return RedirectResponse(url=return_to, status_code=303)
