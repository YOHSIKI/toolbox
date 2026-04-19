"""実行履歴などのリテンション。週次で古いデータを片付ける。"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from app.deps import AppContext
from db.repositories import history_repo

logger = logging.getLogger(__name__)

HISTORY_KEEP_DAYS = 90


def retention_job(context: AppContext) -> None:
    cutoff = datetime.now() - timedelta(days=HISTORY_KEEP_DAYS)
    deleted = history_repo.purge_older_than(context.db_path, cutoff)
    logger.info("retention: purged %d history rows older than %s", deleted, cutoff.date())
