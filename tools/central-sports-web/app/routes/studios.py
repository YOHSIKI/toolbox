"""店舗切替。現在の選択は Cookie で保持する。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse

from app.deps import AppContext, get_context
from app.routes._shared import STUDIO_COOKIE
from db.repositories import studio_repo

router = APIRouter()


@router.post("/studios/switch", name="switch_studio")
def switch_studio(
    studio_pk: int = Form(...),
    next_url: str = Form("/"),
    context: AppContext = Depends(get_context),
) -> RedirectResponse:
    studio = studio_repo.get_studio_by_id(context.db_path, studio_pk)
    if studio is None:
        raise HTTPException(400, detail="店舗が見つかりません")
    response = RedirectResponse(url=next_url or "/", status_code=303)
    response.set_cookie(
        STUDIO_COOKIE,
        str(studio.id),
        max_age=60 * 60 * 24 * 365,
        httponly=True,
        samesite="lax",
    )
    return response
