"""9:00 定期予約実行。

目的:
- その日 9:00 に**新規解放された +6 日後の日**だけを予約ターゲットにする。
  `schedule_open_days=7` は「今日含む 7 日間」= 今日〜今日+6 日を開放するので、
  9:00 に新しく加わるのは `today + 6` の日付（例: 4/19 日曜の 9:00 → 4/25 土曜）。
  （既に開放済みの日は他ユーザーが席を取っている可能性が高く、無駄打ちになる）
- intent（予約予定）と recurring（定期予約）を並列に走らせ、9 時ちょうどに
  取りに行くべき席の取り逃しを減らす。
"""

from __future__ import annotations

import logging
import time as time_mod
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any, Callable

from app.deps import AppContext
from app.domain.entities import HistoryCategory
from app.services.booking_intent import IntentRunResult
from app.services.reserve_recurring import RecurringRunResult
from app.domain.entities import RecurringReservation
from infra.notifier.discord import NotifyLevel

logger = logging.getLogger(__name__)


def run_at_nine_job(context: AppContext) -> None:
    if context.recurring is None:
        logger.info("run_at_nine skipped: not configured")
        return

    today = datetime.now(tz=context.settings.tz).date()
    # schedule_open_days=7 は「今日〜今日+6 日」の 7 日間を開放する仕様。
    # 毎朝 9:00 に新規解放されるのは `today + 6` の日付（例: 日曜 → 土曜、月曜 → 日曜）。
    target_date = today + timedelta(days=6)

    # 事前カウント（ログ用）
    intents_for_target = 0
    if context.booking_intent is not None:
        pending = context.booking_intent.list_pending_upcoming(from_date=today)
        intents_for_target = sum(
            1 for i in pending if i.lesson_date == target_date
        )
    recurrings_for_target = sum(
        1 for r in context.recurring.list_active()
        if r.day_of_week == target_date.weekday()
    )

    started_at = datetime.now()
    logger.info(
        "9:00 job start: target_date=%s, intents_count=%d, recurrings_count=%d",
        target_date, intents_for_target, recurrings_for_target,
    )

    def run_intents() -> list[IntentRunResult]:
        # 並列実行時に 100ms ずらして出す（hacomono 側が同時 POST を拒否した場合の緩和）
        time_mod.sleep(0.0)  # intent 側は idx=0 → 0ms
        assert context.booking_intent is not None
        return context.booking_intent.execute_due(today=today, target_date=target_date)

    def run_recurrings() -> list[tuple[RecurringReservation, RecurringRunResult]]:
        time_mod.sleep(0.1)  # recurring 側は idx=1 → 100ms
        assert context.recurring is not None
        return context.recurring.execute_all_for_today(
            today=today,
            target_date=target_date,
            source_category=HistoryCategory.AUTOMATION,
        )

    tasks: list[tuple[str, Callable[[], Any]]] = []
    if context.booking_intent is not None and intents_for_target > 0:
        tasks.append(("intent", run_intents))
    if recurrings_for_target > 0:
        tasks.append(("recurring", run_recurrings))

    if not tasks:
        logger.info("9:00 job: no targets for %s", target_date)
        return

    intent_results: list[IntentRunResult] = []
    recurring_results: list[tuple[RecurringReservation, RecurringRunResult]] = []
    task_durations: dict[str, int] = {}

    with ThreadPoolExecutor(max_workers=max(2, len(tasks))) as pool:
        future_map = {}
        for name, fn in tasks:
            task_started = datetime.now()
            future_map[pool.submit(fn)] = (name, task_started)

        for fut in as_completed(future_map):
            name, task_started = future_map[fut]
            elapsed_ms = int((datetime.now() - task_started).total_seconds() * 1000)
            task_durations[name] = elapsed_ms
            try:
                result = fut.result()
                if name == "intent":
                    intent_results = result  # type: ignore[assignment]
                    logger.info(
                        "9:00 job task done: name=intent elapsed_ms=%d executed=%d",
                        elapsed_ms, len(intent_results),
                    )
                elif name == "recurring":
                    recurring_results = result  # type: ignore[assignment]
                    logger.info(
                        "9:00 job task done: name=recurring elapsed_ms=%d executed=%d",
                        elapsed_ms, len(recurring_results),
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("9:00 job task failed: name=%s error=%s", name, exc)

    # 通知メッセージ構築（recurring 分のみ従来どおりサマリを出す）
    success = warning = failure = 0
    details: list[str] = []
    for item, result in recurring_results:
        if (
            result.ok
            and result.seat_no is not None
            and item.seat_preferences
            and item.seat_preferences[0] == result.seat_no
        ):
            success += 1
            details.append(f"✅ {item.program_name} 席 {result.seat_no:02d}")
        elif result.ok:
            warning += 1
            seat_label = f"{result.seat_no:02d}" if result.seat_no is not None else "-"
            details.append(f"⚠ {item.program_name} 席 {seat_label}（代替席）")
        else:
            failure += 1
            details.append(f"✗ {item.program_name}: {result.message}")

    total_elapsed_ms = int((datetime.now() - started_at).total_seconds() * 1000)
    logger.info(
        "9:00 job end: target_date=%s total_ms=%d intent_ms=%s recurring_ms=%s "
        "intent_executed=%d recurring_success=%d recurring_warning=%d recurring_failure=%d",
        target_date, total_elapsed_ms,
        task_durations.get("intent"), task_durations.get("recurring"),
        len(intent_results), success, warning, failure,
    )

    if not recurring_results:
        # recurring 対象が無ければ従来どおり通知は出さない（intent だけの日は静かに終わる）
        return

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
            f"成功 {success} 件 / 代替 {warning} 件 / 失敗 {failure} 件\n"
            + "\n".join(details)
        ),
        level=level,
    )
