"""9:00 定期予約実行。

目的:
- その日 9:00 に**新規解放された +6 日後の日**だけを予約ターゲットにする。
  `schedule_open_days=7` は「今日含む 7 日間」= 今日〜今日+6 日を開放するので、
  9:00 に新しく加わるのは `today + 6` の日付（例: 4/19 日曜の 9:00 → 4/25 土曜）。
  （既に開放済みの日は他ユーザーが席を取っている可能性が高く、無駄打ちになる）
- intent（予約予定）と recurring（定期予約）を**全件並列**で走らせ、9 時ちょうどに
  取りに行くべき席の取り逃しを減らす。各タスクには index × 50ms の sleep オフセットを
  入れて hacomono 側の瞬間的ピークを緩和する。
"""

from __future__ import annotations

import logging
import time as time_mod
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

from app.deps import AppContext
from app.domain.entities import BookingIntent, HistoryCategory, RecurringReservation
from app.services.booking_intent import IntentRunResult
from app.services.reserve_recurring import RecurringRunResult
from infra.notifier.discord import NotifyLevel
from scheduler.jobs.cache_refresh import cache_refresh_job

logger = logging.getLogger(__name__)

# 同時 POST の瞬間ピークを緩和するためのオフセット（index × この秒数）。
_STAGGER_STEP_SEC = 0.05


def run_at_nine_job(context: AppContext) -> None:
    """9:00 の予約実行ラッパー。

    予約の有無・成否・例外に関わらず、最後に必ず cache_refresh_job を呼んで
    新規解放された +6 日後のレッスンを含む最新スケジュールで時間割キャッシュを
    更新する（= 画面に実データを反映）。予約計画がない日もこの経路で日次更新される。
    """

    try:
        _run_at_nine_core(context)
    finally:
        try:
            cache_refresh_job(context)
            logger.info("9:00 post: cache refreshed")
        except Exception as exc:  # noqa: BLE001
            logger.warning("9:00 post cache refresh failed: %s", exc)


def _run_at_nine_core(context: AppContext) -> None:
    if context.recurring is None:
        logger.info("run_at_nine skipped: not configured")
        return

    today = datetime.now(tz=context.settings.tz).date()
    # 9:00 直前の cache_refresh で stale な fetch_week が cache に入っている可能性がある。
    # 予約実行の前に強制 invalidate して、新規解放された target_date を確実に拾う。
    if context.gateway is not None and hasattr(context.gateway, "invalidate_caches"):
        try:
            context.gateway.invalidate_caches()
        except Exception as exc:  # noqa: BLE001
            logger.warning("pre-9:00 invalidate_caches failed: %s", exc)
    # schedule_open_days=7 は「今日〜今日+6 日」の 7 日間を開放する仕様。
    # 毎朝 9:00 に新規解放されるのは `today + 6` の日付（例: 日曜 → 土曜、月曜 → 日曜）。
    target_date = today + timedelta(days=6)

    # 対象の intent / recurring を抽出（並列実行のため全件リストアップ）
    intent_targets: list[BookingIntent] = []
    if context.booking_intent is not None:
        intent_targets = context.booking_intent.list_runnable(
            today=today, target_date=target_date
        )
    resolved_target, recurring_targets = context.recurring.list_targets_for_date(
        today=today, target_date=target_date
    )

    started_at = datetime.now()
    logger.info(
        "9:00 job start: target_date=%s, intents_count=%d, recurrings_count=%d",
        target_date, len(intent_targets), len(recurring_targets),
    )

    total_tasks = len(intent_targets) + len(recurring_targets)
    if total_tasks == 0:
        logger.info("9:00 job: no targets for %s", target_date)
        return

    intent_service = context.booking_intent
    recurring_service = context.recurring

    def run_intent(index: int, intent: BookingIntent) -> IntentRunResult:
        time_mod.sleep(index * _STAGGER_STEP_SEC)
        assert intent_service is not None
        return intent_service.execute_one(intent)

    def run_recurring(
        index: int, item: RecurringReservation
    ) -> tuple[RecurringReservation, RecurringRunResult]:
        time_mod.sleep(index * _STAGGER_STEP_SEC)
        assert recurring_service is not None
        result = recurring_service.execute_one_for_date(
            item,
            target_date=resolved_target,
            source_category=HistoryCategory.AUTOMATION,
        )
        return item, result

    intent_results: list[IntentRunResult] = []
    recurring_results: list[tuple[RecurringReservation, RecurringRunResult]] = []

    with ThreadPoolExecutor(max_workers=total_tasks) as pool:
        future_map: dict[object, tuple[str, datetime]] = {}
        # index はグローバル連番（intent → recurring の順）。オフセット衝突を避ける。
        idx = 0
        for intent in intent_targets:
            task_started = datetime.now()
            future = pool.submit(run_intent, idx, intent)
            future_map[future] = (f"intent:{intent.id}", task_started)
            idx += 1
        for item in recurring_targets:
            task_started = datetime.now()
            future = pool.submit(run_recurring, idx, item)
            future_map[future] = (f"recurring:{item.id}", task_started)
            idx += 1

        for fut in as_completed(future_map):
            name, task_started = future_map[fut]
            elapsed_ms = int((datetime.now() - task_started).total_seconds() * 1000)
            try:
                result = fut.result()
                if name.startswith("intent:"):
                    intent_results.append(result)  # type: ignore[arg-type]
                    logger.info(
                        "9:00 task done: %s elapsed_ms=%d ok=%s seat=%s",
                        name, elapsed_ms,
                        result.ok,  # type: ignore[attr-defined]
                        result.seat_no,  # type: ignore[attr-defined]
                    )
                else:
                    recurring_results.append(result)  # type: ignore[arg-type]
                    _, r_result = result  # type: ignore[misc]
                    logger.info(
                        "9:00 task done: %s elapsed_ms=%d ok=%s seat=%s",
                        name, elapsed_ms, r_result.ok, r_result.seat_no,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("9:00 task failed: %s error=%s", name, exc)

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
        "9:00 job end: target_date=%s total_ms=%d "
        "intent_executed=%d recurring_success=%d recurring_warning=%d recurring_failure=%d",
        target_date, total_elapsed_ms,
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
