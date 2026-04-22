"""実行履歴などのリテンション。週次で古いデータを片付ける。"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from app.deps import AppContext
from db.repositories import history_repo

logger = logging.getLogger(__name__)


def retention_job(context: AppContext) -> None:
    keep_days = context.settings.history_keep_days
    cutoff = datetime.now() - timedelta(days=keep_days)
    deleted = history_repo.purge_older_than(context.db_path, cutoff)
    logger.info(
        "retention: purged %d history rows older than %s (keep=%d days)",
        deleted, cutoff.date(), keep_days,
    )
