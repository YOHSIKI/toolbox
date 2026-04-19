"""手動同期（本家マイページの予約一覧をすぐに取り込む）。"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse

from app.deps import AppContext, get_context

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/sync", name="sync_now")
def sync_now(
    context: AppContext = Depends(get_context),
) -> RedirectResponse:
    if context.sync_reservations is None:
        raise HTTPException(503, "認証情報が未設定のため同期できません")
    try:
        context.sync_reservations.run()
        context.sync_reservations._last_sync_ts = time.monotonic()
    except Exception as exc:  # noqa: BLE001
        logger.warning("manual sync failed: %s", exc)
    return RedirectResponse(url="/", status_code=303)
