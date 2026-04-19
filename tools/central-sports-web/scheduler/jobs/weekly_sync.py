"""週次で今週の実スケジュールを取り直してキャッシュ更新。"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta

from app.deps import AppContext
from app.domain.entities import (
    HistoryCategory,
    HistoryEntry,
    HistoryResult,
    StudioRef,
)
from db.repositories import history_repo, schedule_cache_repo, studio_repo

logger = logging.getLogger(__name__)


def weekly_sync_job(context: AppContext) -> None:
    if context.gateway is None:
        logger.info("weekly_sync skipped: not configured")
        return
    today = datetime.now(tz=context.settings.tz).date()
    week_start = today - timedelta(days=today.weekday())
    studios = studio_repo.list_studios(context.db_path)
    total = 0
    errors = 0
    for studio in studios:
        try:
            lessons = context.gateway.fetch_week(
                StudioRef(studio.studio_id, studio.studio_room_id),
                week_start,
            )
            total += len(lessons)
            cache_key = f"weekly:{studio.studio_id}:{studio.studio_room_id}:{week_start.isoformat()}"
            schedule_cache_repo.put(
                context.db_path,
                cache_key,
                "weekly_sync",
                {"lesson_count": len(lessons)},
                ttl=timedelta(days=7),
            )
        except Exception as exc:  # noqa: BLE001
            errors += 1
            logger.warning(
                "weekly_sync failed studio=%d: %s", studio.studio_id, exc
            )
    history_repo.insert(
        context.db_path,
        HistoryEntry(
            id=None,
            request_id=uuid.uuid4().hex[:12],
            occurred_at=datetime.now(),
            category=HistoryCategory.WEEKLY_SYNC,
            endpoint="schedule.weekly",
            elapsed_ms=None,
            result=HistoryResult.SUCCESS if errors == 0 else HistoryResult.WARNING,
            message=f"週次スケジュール同期: {total} 件（エラー {errors} 件）",
            metadata={
                "week_start": week_start.isoformat(),
                "errors": errors,
            },
        ),
    )
