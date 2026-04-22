"""APScheduler の起動と停止。

- AppContext を受け取り、ジョブを context 付きで登録
- BackgroundScheduler で FastAPI のループを阻害しない
- 同時ジョブ数は 1 に制限（SQLite の書き込み競合を避けるため）
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.deps import AppContext
from scheduler.jobs.cache_refresh import cache_refresh_job
from scheduler.jobs.daily_sync import daily_sync_job
from scheduler.jobs.retention import retention_job
from scheduler.jobs.run_at_nine import run_at_nine_job
from scheduler.jobs.warmup import warmup_job

logger = logging.getLogger(__name__)


def start_scheduler(context: AppContext) -> BackgroundScheduler:
    settings = context.settings
    scheduler = BackgroundScheduler(
        timezone=settings.timezone,
        job_defaults={
            "coalesce": True,
            "max_instances": 1,
            # 定時ジョブの発火遅延許容秒数。運用で触る価値が無いので固定値。
            "misfire_grace_time": 60,
        },
    )

    scheduler.add_job(
        warmup_job,
        trigger=CronTrigger(
            day_of_week="mon-sun",
            hour=settings.login_warmup_hour,
            minute=settings.login_warmup_minute,
        ),
        args=[context],
        id="login_warmup",
        name="8:55 事前ログイン・対象解決",
        replace_existing=True,
    )

    scheduler.add_job(
        run_at_nine_job,
        trigger=CronTrigger(
            day_of_week="mon-sun",
            hour=settings.auto_booking_hour,
            minute=settings.auto_booking_minute,
        ),
        args=[context],
        id="auto_booking",
        name="9:00 定期予約実行",
        replace_existing=True,
    )

    # 深夜 00:05 に 1 日 1 回だけ時間割キャッシュを更新する（my_reservations_sync の 00:00 直後）。
    # これ以降は日中に外部 API を叩かず、ユーザー操作時は常時 cache hit で速い。
    # 朝 9:00 の新規解放分は auto_booking が finally で cache_refresh_job を呼ぶので
    # ここでは担当しない。alias 自動学習と、公開月間 API 経由での座席レイアウト
    # （gateway 内部の _hint_full / _hint_name / _hint_time 索引）の更新も同時に走る。
    scheduler.add_job(
        cache_refresh_job,
        trigger=CronTrigger(
            hour=settings.schedule_refresh_hour,
            minute=settings.schedule_refresh_minute,
        ),
        args=[context],
        id="schedule_refresh",
        name="時間割キャッシュ温め直し（日次）",
        replace_existing=True,
    )

    scheduler.add_job(
        daily_sync_job,
        trigger=CronTrigger(
            hour=settings.my_reservations_sync_hour,
            minute=settings.my_reservations_sync_minute,
        ),
        args=[context],
        id="my_reservations_sync",
        name="日次 予約一覧同期",
        replace_existing=True,
    )

    # 履歴クリーンアップは毎日 0 時固定で走らせる。`history_keep_days` を
    # 超えた履歴をその場で削除するため、曜日や時刻を変える意味がない。
    scheduler.add_job(
        retention_job,
        trigger=CronTrigger(hour=0, minute=0),
        args=[context],
        id="history_retention",
        name="履歴クリーンアップ（日次 00:00）",
        replace_existing=True,
    )

    scheduler.start()
    for job in scheduler.get_jobs():
        logger.info("scheduled id=%s next=%s", job.id, job.next_run_time)
    return scheduler
