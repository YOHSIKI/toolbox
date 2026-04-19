"""9:00 定期予約実行。"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from app.deps import AppContext
from app.domain.entities import HistoryCategory
from infra.notifier.discord import NotifyLevel

logger = logging.getLogger(__name__)


def run_at_nine_job(context: AppContext) -> None:
    if context.recurring is None:
        logger.info("run_at_nine skipped: not configured")
        return
    today = datetime.now(tz=context.settings.tz).date()
    target_date = today + timedelta(days=7)  # 予約開放は 1 週間先まで
    items = context.recurring.list_active()
    targets = [i for i in items if i.day_of_week == target_date.weekday()]
    if not targets:
        logger.info("no recurring targets for %s", target_date)
        return

    success = warning = failure = 0
    details: list[str] = []
    for item in targets:
        result = context.recurring.execute_one(
            item.id,
            target_date=target_date,
            source_category=HistoryCategory.AUTOMATION,
        )
        if result.ok and result.seat_no is not None and item.seat_preferences and item.seat_preferences[0] == result.seat_no:
            success += 1
            details.append(f"✅ {item.program_name} 席 {result.seat_no:02d}")
        elif result.ok:
            warning += 1
            seat_label = f"{result.seat_no:02d}" if result.seat_no is not None else "-"
            details.append(f"⚠ {item.program_name} 席 {seat_label}（代替席）")
        else:
            failure += 1
            details.append(f"✗ {item.program_name}: {result.message}")

    level = (
        NotifyLevel.DANGER if failure > 0
        else NotifyLevel.WARNING if warning > 0
        else NotifyLevel.SUCCESS
    )
    title = (
        "予約で失敗がありました" if failure > 0
        else "予約は完了しましたが一部は代替席" if warning > 0
        else "本日分の予約がすべて完了しました"
    )
    context.notifier.send(
        title=title,
        description=(
            f"成功 {success} 件 / 代替 {warning} 件 / 失敗 {failure} 件\n" + "\n".join(details)
        ),
        level=level,
    )
