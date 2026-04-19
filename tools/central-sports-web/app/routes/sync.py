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
    # 同期直後は「最新情報を見たい」という意図なので、gateway の週/月キャッシュを
    # invalidate して次の画面描画で必ず再取得させる。学習済み座席配置 / hint は
    # in-memory + DB に残るので表示速度は落ちない。
    if context.gateway is not None:
        try:
            context.gateway.invalidate_caches()  # type: ignore[attr-defined]
        except AttributeError:
            pass
    return RedirectResponse(url="/", status_code=303)
