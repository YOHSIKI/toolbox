"""定期予約タブの画面と CRUD。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.deps import AppContext, get_context
from app.domain.entities import DAY_OF_WEEK_LABEL, RecurringStatus
from app.routes._shared import resolve_current_studio
from app.services.dashboard_query import run_schedule_label

router = APIRouter()

BASE_DIR = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "ui" / "templates"))
templates.env.globals["run_schedule_label"] = run_schedule_label


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
    # 重複を順序を保って除去
    seen: set[int] = set()
    deduped: list[int] = []
    for s in seats:
        if s in seen:
            continue
        seen.add(s)
        deduped.append(s)
    return deduped


def _label_for_offset(delta_weeks: int) -> str:
    if delta_weeks == 0:
        return "今週"
    if delta_weeks == 1:
        return "来週"
    return f"{delta_weeks} 週間後"


@router.get("/reserve/recurring", response_class=HTMLResponse, name="recurring_list")
def recurring_list(
    request: Request,
    selected: str | None = None,
    context: AppContext = Depends(get_context),
) -> HTMLResponse:
    today = datetime.now(tz=context.settings.tz).date()
    studio = resolve_current_studio(request, context.db_path)

    from db.repositories import recurring_repo

    error = None
    items = recurring_repo.list_recurring(context.db_path)
    current = None
    occurrences: list = []
    if context.recurring is None:
        error = "認証情報が未設定のため、定期予約の実行が停止しています。"
    else:
        current = context.recurring.pick_default(selected)
        if current is not None:
            try:
                raw = context.recurring.build_occurrences(current, today=today)
            except Exception as exc:  # noqa: BLE001
                error = f"配置プレビューを取得できませんでした: {exc}"
                raw = []
            for occ in raw:
                delta_weeks = (occ.lesson_date - today).days // 7
                occurrences.append((occ, _label_for_offset(delta_weeks)))

    return templates.TemplateResponse(
        request,
        "reserve_recurring.html",
        {
            "active_nav": "reserve",
            "active_tab": "recurring",
            "topbar_title": "予約",
            "topbar_meta": studio.display_name if studio else "",
            "studio": studio,
            "items": items,
            "current": current,
            "occurrences": occurrences,
            "today": today,
            "error": error,
            "context": context,
            "settings": context.settings,
        },
    )


@router.get("/reserve/recurring/new", response_class=HTMLResponse, name="recurring_new")
def recurring_new(
    request: Request,
    context: AppContext = Depends(get_context),
) -> HTMLResponse:
    from collections import defaultdict
    from datetime import timedelta

    from app.domain.entities import StudioRef

    studio = resolve_current_studio(request, context.db_path)
    weekdays = [(i, f"{label}曜") for i, label in enumerate(DAY_OF_WEEK_LABEL)]

    # 曜日 -> 時刻 -> [(program_id, program_name, capacity)] の map を構築
    # 選択中店舗の今日〜2週間分のスケジュールから実在する組み合わせだけを出す
    weekday_time_programs: dict[int, dict[str, list[tuple[str, str, int | None]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    default_capacity = 35
    error = None
    if studio is not None and context.gateway is not None:
        today = datetime.now(tz=context.settings.tz).date()
        seen: set[tuple[int, str, str]] = set()
        for week_offset in (0, 1):
            week_start = today + timedelta(days=week_offset * 7)
            try:
                lessons = context.gateway.fetch_week(
                    StudioRef(studio.studio_id, studio.studio_room_id),
                    week_start,
                )
            except Exception as exc:  # noqa: BLE001
                error = f"プログラム候補を取得できませんでした: {exc}"
                lessons = []
            for lesson in lessons:
                wd = lesson.lesson_date.weekday()
                key = (wd, lesson.start_time, lesson.program_id)
                if key in seen:
                    continue
                seen.add(key)
                weekday_time_programs[wd][lesson.start_time].append(
                    (lesson.program_id, lesson.program_name, lesson.capacity)
                )
                if lesson.capacity:
                    default_capacity = max(default_capacity, lesson.capacity)

    # JSON にしてテンプレートへ
    import json as _json

    weekday_time_programs_json = _json.dumps(
        {
            str(wd): {
                t: programs for t, programs in sorted(times.items())
            }
            for wd, times in weekday_time_programs.items()
        },
        ensure_ascii=False,
    )

    return templates.TemplateResponse(
        request,
        "reserve_recurring_form.html",
        {
            "active_nav": "reserve",
            "active_tab": "recurring",
            "topbar_title": "定期予約の追加",
            "topbar_meta": studio.display_name if studio else "",
            "studio": studio,
            "weekdays": weekdays,
            "weekday_time_programs_json": weekday_time_programs_json,
            "default_capacity": default_capacity,
            "error": error,
            "context": context,
        },
    )


@router.post("/reserve/recurring", name="recurring_create")
def recurring_create(
    request: Request,
    day_of_week: int = Form(...),
    start_time: str = Form(...),
    program_id: str = Form(...),
    program_name: str = Form(...),
    seat_preferences: str = Form(""),
    note: str | None = Form(None),
    context: AppContext = Depends(get_context),
) -> RedirectResponse:
    if context.recurring is None:
        raise HTTPException(503, "認証情報が未設定のため定期予約を登録できません")
    studio = resolve_current_studio(request, context.db_path)
    if studio is None:
        raise HTTPException(500, "店舗が登録されていません")
    seats = _parse_seat_preferences(seat_preferences)
    item = context.recurring.create(
        day_of_week=int(day_of_week),
        start_time=start_time,
        program_id=program_id,
        program_name=program_name or program_id,
        studio_id=studio.studio_id,
        studio_room_id=studio.studio_room_id,
        seat_preferences=seats,
        note=note,
    )
    return RedirectResponse(
        url=f"/reserve/recurring?selected={item.id}", status_code=303
    )


@router.post("/reserve/recurring/{recurring_id}/pause", name="recurring_pause")
def recurring_pause(
    recurring_id: str,
    context: AppContext = Depends(get_context),
) -> RedirectResponse:
    if context.recurring is None:
        raise HTTPException(503, "認証情報が未設定のため操作できません")
    context.recurring.set_status(recurring_id, RecurringStatus.PAUSED)
    return RedirectResponse(url=f"/reserve/recurring?selected={recurring_id}", status_code=303)


@router.post("/reserve/recurring/{recurring_id}/resume", name="recurring_resume")
def recurring_resume(
    recurring_id: str,
    context: AppContext = Depends(get_context),
) -> RedirectResponse:
    if context.recurring is None:
        raise HTTPException(503, "認証情報が未設定のため操作できません")
    context.recurring.set_status(recurring_id, RecurringStatus.ACTIVE)
    return RedirectResponse(url=f"/reserve/recurring?selected={recurring_id}", status_code=303)


@router.post("/reserve/recurring/{recurring_id}/delete", name="recurring_delete")
def recurring_delete(
    recurring_id: str,
    context: AppContext = Depends(get_context),
) -> RedirectResponse:
    if context.recurring is None:
        raise HTTPException(503, "認証情報が未設定のため操作できません")
    context.recurring.set_status(recurring_id, RecurringStatus.DELETED)
    return RedirectResponse(url="/reserve/recurring", status_code=303)


@router.post("/reserve/recurring/{recurring_id}/run", name="recurring_run")
def recurring_run(
    recurring_id: str,
    target_date_raw: str = Form(..., alias="target_date"),
    context: AppContext = Depends(get_context),
) -> RedirectResponse:
    if context.recurring is None:
        raise HTTPException(503, "認証情報が未設定のため実行できません")
    from datetime import date

    from app.domain.entities import HistoryCategory

    try:
        target_date = date.fromisoformat(target_date_raw)
    except ValueError as exc:
        raise HTTPException(400, "日付の形式が不正です") from exc
    context.recurring.execute_one(
        recurring_id,
        target_date=target_date,
        source_category=HistoryCategory.MANUAL,
    )
    return RedirectResponse(
        url=f"/reserve/recurring?selected={recurring_id}", status_code=303
    )
