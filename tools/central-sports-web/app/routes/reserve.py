"""単発予約（カレンダー）画面と操作。"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.deps import AppContext, get_context
from app.domain.errors import NotFound
from app.routes._shared import resolve_current_studio
from app.services.calendar_query import Selection, resolve_week_start
from app.templating import render_page
from db.repositories import studio_repo

router = APIRouter()

BASE_DIR = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "ui" / "templates"))

_WEEKDAY_LABELS = ["月", "火", "水", "木", "金", "土", "日"]


@router.get("/reserve", response_class=HTMLResponse, name="reserve_calendar")
def reserve_calendar(
    request: Request,
    week: str | None = Query(None),
    date_: str | None = Query(None, alias="date"),
    program: str | None = Query(None),
    time: str | None = Query(None),
    context: AppContext = Depends(get_context),
) -> HTMLResponse:
    # URL 側は `?date=` のままにしつつ、関数内は予約語衝突を避けるため `date_`
    today = datetime.now(tz=context.settings.tz).date()
    week_start = resolve_week_start(week, today=today)

    studio = resolve_current_studio(request, context.db_path)
    studios = studio_repo.list_studios(context.db_path)
    if studio is None:
        raise HTTPException(500, "店舗が登録されていません")

    selection = Selection()
    if date_:
        try:
            selection.lesson_date = date.fromisoformat(date_)
        except ValueError:
            selection.lesson_date = None
    selection.program_id = program
    selection.lesson_time = time

    view = None
    error = None
    if context.calendar is not None:
        try:
            view = context.calendar.build_week(
                studio=studio,
                week_start=week_start,
                today=today,
                selection=selection,
            )
        except Exception as exc:  # noqa: BLE001
            error = f"スケジュールを取得できませんでした: {exc}"
    else:
        error = "認証情報が未設定のため、スケジュールを取得できません。"

    from datetime import timedelta

    prev_week = (week_start - timedelta(weeks=1)).isoformat()
    next_week = (week_start + timedelta(weeks=1)).isoformat()

    return render_page(
        templates,
        request,
        "reserve_calendar.html",
        {
            "active_nav": "reserve",
            "active_tab": "single",
            "topbar_title": "予約",
            "topbar_meta": studio.display_name,
            "studio": studio,
            "studios": studios,
            "view": view,
            "error": error,
            "prev_week": prev_week,
            "next_week": next_week,
            "weekday_labels": _WEEKDAY_LABELS,
            "today": today,
            "settings": context.settings,
            "context": context,
            "next_url": str(request.url.path) + (f"?{request.url.query}" if request.url.query else ""),
        },
    )


@router.post("/reserve/create", name="reserve_create")
def reserve_create(
    request: Request,
    studio_lesson_id: int = Form(...),
    lesson_date: str = Form(...),
    lesson_time: str = Form(...),
    program_id: str = Form(...),
    program_name: str = Form(...),
    instructor_name: str | None = Form(None),
    seat_no: int = Form(...),
    context: AppContext = Depends(get_context),
) -> RedirectResponse:
    if context.reserve_single is None:
        raise HTTPException(503, "認証情報が未設定のため予約できません")
    studio = resolve_current_studio(request, context.db_path)
    if studio is None:
        raise HTTPException(500, "店舗が登録されていません")
    try:
        lesson_date_value = date.fromisoformat(lesson_date)
    except ValueError as exc:
        raise HTTPException(400, "日付の形式が不正です") from exc
    attempt = context.reserve_single.create(
        studio_lesson_id=studio_lesson_id,
        lesson_date=lesson_date_value,
        lesson_time=lesson_time,
        program_id=program_id,
        program_name=program_name,
        instructor_name=instructor_name,
        studio_id=studio.studio_id,
        studio_room_id=studio.studio_room_id,
        seat_no=seat_no,
    )
    # 予約完了後は元の週（クエリ ?week=）に留まる。未指定なら今日起点。
    from urllib.parse import urlencode

    referer_week = request.headers.get("referer", "")
    qs: dict[str, str] = {
        "date": lesson_date_value.isoformat(),
        "program": program_id,
        "time": lesson_time,
        "flash": "success" if attempt.ok else "failure",
    }
    # ?week= を Referer から拾って維持（予約対象日に飛ばさない）
    try:
        from urllib.parse import parse_qs, urlparse

        referer_qs = parse_qs(urlparse(referer_week).query)
        if referer_qs.get("week"):
            qs["week"] = referer_qs["week"][0]
    except Exception:  # noqa: BLE001
        pass
    return RedirectResponse(url="/reserve?" + urlencode(qs), status_code=303)


@router.post("/reserve/{reservation_id}/cancel", name="reserve_cancel")
def reserve_cancel(
    reservation_id: str,
    request: Request,
    return_to: str | None = Form(None),
    context: AppContext = Depends(get_context),
) -> RedirectResponse:
    if context.reserve_single is None:
        raise HTTPException(503, "認証情報が未設定のため取消できません")
    try:
        context.reserve_single.cancel(reservation_id)
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    target = return_to or "/"
    return RedirectResponse(url=target, status_code=303)


@router.post("/reserve/{reservation_id}/seat", name="reserve_change_seat")
def reserve_change_seat(
    reservation_id: str,
    request: Request,
    new_seat_no: int = Form(...),
    return_to: str | None = Form(None),
    context: AppContext = Depends(get_context),
) -> RedirectResponse:
    if context.reserve_single is None:
        raise HTTPException(503, "認証情報が未設定のため席変更できません")
    try:
        context.reserve_single.change_seat(reservation_id, new_seat_no)
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    target = return_to or "/"
    return RedirectResponse(url=target, status_code=303)
