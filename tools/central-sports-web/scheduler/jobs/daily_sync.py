"""毎日 0:00 に、自分の予約一覧を本家マイページから取り込む。"""

from __future__ import annotations

import logging

from app.deps import AppContext

logger = logging.getLogger(__name__)


def daily_sync_job(context: AppContext) -> None:
    if context.sync_reservations is None:
        logger.info("daily_sync skipped: not configured")
        return
    try:
        context.sync_reservations.run()
    except Exception as exc:  # noqa: BLE001
        logger.warning("daily_sync failed: %s", exc)
