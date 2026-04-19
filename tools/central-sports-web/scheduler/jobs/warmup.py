"""8:55 事前ログイン + 対象解決 + 予約一覧同期。"""

from __future__ import annotations

import logging
from datetime import datetime

from app.deps import AppContext

logger = logging.getLogger(__name__)


def warmup_job(context: AppContext) -> None:
    if context.warmup is None:
        logger.info("warmup skipped: not configured")
        return
    today = datetime.now(tz=context.settings.tz).date()
    context.warmup.run(today=today)
    if context.sync_reservations is not None:
        try:
            context.sync_reservations.run()
        except Exception as exc:  # noqa: BLE001
            logger.warning("sync reservations failed during warmup: %s", exc)
