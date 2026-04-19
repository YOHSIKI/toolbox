"""APScheduler の起動と停止。

- AppContext を受け取り、ジョブを context 付きで登録
- BackgroundScheduler で FastAPI のループを阻害しない
- 同時ジョブ数は 1 に制限（SQLite の書き込み競合を避けるため）
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.deps import AppContext
from scheduler.jobs.cache_refresh import cache_refresh_job
from scheduler.jobs.daily_sync import daily_sync_job
from scheduler.jobs.monthly_sync import monthly_sync_job
from scheduler.jobs.retention import retention_job
from scheduler.jobs.run_at_nine import run_at_nine_job
from scheduler.jobs.warmup import warmup_job
from scheduler.jobs.weekly_sync import weekly_sync_job

logger = logging.getLogger(__name__)


def start_scheduler(context: AppContext) -> BackgroundScheduler:
    settings = context.settings
    scheduler = BackgroundScheduler(
        timezone=settings.timezone,
        job_defaults={
            "coalesce": True,
            "max_instances": 1,
            "misfire_grace_time": settings.misfire_grace_time_seconds,
        },
    )

    scheduler.add_job(
        warmup_job,
        trigger=CronTrigger(
            day_of_week="mon-sun",
            hour=settings.warm_up_hour,
            minute=settings.warm_up_minute,
        ),
        args=[context],
        id="warmup",
        name="8:55 事前ログイン・対象解決",
        replace_existing=True,
    )

    scheduler.add_job(
        run_at_nine_job,
        trigger=CronTrigger(
            day_of_week="mon-sun",
            hour=settings.run_hour,
            minute=settings.run_minute,
        ),
        args=[context],
        id="run_at_nine",
        name="9:00 定期予約実行",
        replace_existing=True,
    )

    # 2 分毎に reserve API 窓のキャッシュを温め直す。ユーザーからのアクセス時に
    # ほぼ常に cache hit させ、schedule 再取得のスパイク（300-500ms）を隠す。
    scheduler.add_job(
        cache_refresh_job,
        trigger=IntervalTrigger(minutes=2),
        args=[context],
        id="cache_refresh",
        name="キャッシュ温め直し（2 分毎）",
        replace_existing=True,
    )

    scheduler.add_job(
        monthly_sync_job,
        # 公開月間 API は認証不要で IP ベースしか追跡されない公開 JSONP なので
        # 毎日叩いて座席レイアウト（_weekday_time_space）を更新する。
        trigger=CronTrigger(hour=3, minute=0),
        args=[context],
        id="monthly_sync",
        name="月次スケジュール同期（公開月間 API、日次）",
        replace_existing=True,
    )

    scheduler.add_job(
        weekly_sync_job,
        trigger=CronTrigger(day_of_week="mon", hour=3, minute=30),
        args=[context],
        id="weekly_sync",
        name="週次スケジュール同期",
        replace_existing=True,
    )

    scheduler.add_job(
        daily_sync_job,
        trigger=CronTrigger(hour=0, minute=0),
        args=[context],
        id="daily_sync",
        name="日次 予約一覧同期",
        replace_existing=True,
    )

    scheduler.add_job(
        retention_job,
        trigger=CronTrigger(day_of_week="sun", hour=4, minute=0),
        args=[context],
        id="retention",
        name="履歴クリーンアップ",
        replace_existing=True,
    )

    scheduler.start()
    for job in scheduler.get_jobs():
        logger.info("scheduled id=%s next=%s", job.id, job.next_run_time)
    return scheduler
