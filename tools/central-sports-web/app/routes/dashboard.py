"""ダッシュボード画面。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.deps import AppContext, get_context
from app.routes._shared import resolve_current_studio
from app.services.dashboard_query import (
    relative_log_time,
    run_schedule_label,
)
from app.templating import render_page
from db.repositories import studio_repo

router = APIRouter()

BASE_DIR = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "ui" / "templates"))
templates.env.globals["run_schedule_label"] = run_schedule_label
templates.env.globals["relative_log_time"] = relative_log_time


@router.get("/", response_class=HTMLResponse, name="dashboard")
def dashboard(
    request: Request,
    context: AppContext = Depends(get_context),
) -> HTMLResponse:
    now = datetime.now(tz=context.settings.tz)
    today = now.date()
    data = context.dashboard.build(today=today, now=now)
    studios = studio_repo.list_studios(context.db_path)
    current_studio = resolve_current_studio(request, context.db_path)

    return render_page(
        templates,
        request,
        "dashboard.html",
        {
            "active_nav": "dashboard",
            "topbar_title": "ダッシュボード",
            "topbar_meta": current_studio.display_name if current_studio else "",
            "today": today,
            "data": data,
            "studios": studios,
            "current_studio": current_studio,
            "settings": context.settings,
            "context": context,
            "next_url": str(request.url.path),
        },
    )
