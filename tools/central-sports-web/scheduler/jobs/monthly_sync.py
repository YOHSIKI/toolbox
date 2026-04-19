"""月初のスケジュール全件取得（各店舗 × 翌月分）。

hacomono の schedule API を date_from=月初 / date_to=月末 で叩き、
`schedule_cache` に TTL=1 週間で積む。公開月間 API（JSONP）は別 Issue で。
"""

from __future__ import annotations

import calendar as _calendar
import logging
import uuid
from datetime import date, datetime, timedelta

from app.deps import AppContext
from app.domain.entities import (
    HistoryCategory,
    HistoryEntry,
    HistoryResult,
    StudioRef,
)
from db.repositories import history_repo, schedule_cache_repo, studio_repo

logger = logging.getLogger(__name__)


def monthly_sync_job(context: AppContext) -> None:
    if context.gateway is None:
        logger.info("monthly_sync skipped: not configured")
        return
    today = datetime.now(tz=context.settings.tz).date()
    first_of_next = _first_of_next_month(today)
    last_day = _calendar.monthrange(first_of_next.year, first_of_next.month)[1]
    month_end = first_of_next.replace(day=last_day)

    studios = studio_repo.list_studios(context.db_path)
    total_lessons = 0
    error_count = 0
    for studio in studios:
        # 週単位で取得（1 ヶ月の範囲を 5 回ほど）
        cursor = first_of_next
        while cursor <= month_end:
            week_end = min(cursor + timedelta(days=6), month_end)
            try:
                lessons = context.gateway.fetch_week(
                    StudioRef(studio.studio_id, studio.studio_room_id),
                    cursor,
                )
                total_lessons += len(lessons)
                cache_key = f"weekly:{studio.studio_id}:{studio.studio_room_id}:{cursor.isoformat()}"
                schedule_cache_repo.put(
                    context.db_path,
                    cache_key,
                    "monthly_sync",
                    {"lesson_count": len(lessons)},
                    ttl=timedelta(days=7),
                )
            except Exception as exc:  # noqa: BLE001
                error_count += 1
                logger.warning("monthly_sync fetch failed studio=%d range=%s~%s: %s",
                               studio.studio_id, cursor, week_end, exc)
            cursor = week_end + timedelta(days=1)

    history_repo.insert(
        context.db_path,
        HistoryEntry(
            id=None,
            request_id=uuid.uuid4().hex[:12],
            occurred_at=datetime.now(),
            category=HistoryCategory.MONTHLY_SYNC,
            endpoint="schedule.weekly",
            elapsed_ms=None,
            result=(
                HistoryResult.FAILURE if error_count and total_lessons == 0
                else HistoryResult.WARNING if error_count
                else HistoryResult.SUCCESS
            ),
            message=f"月間スケジュール同期: {total_lessons} 件（エラー {error_count} 件）",
            metadata={
                "month": first_of_next.isoformat()[:7],
                "studios": len(studios),
                "errors": error_count,
            },
        ),
    )


def _first_of_next_month(today: date) -> date:
    if today.month == 12:
        return date(today.year + 1, 1, 1)
    return date(today.year, today.month + 1, 1)
