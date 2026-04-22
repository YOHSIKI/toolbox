"""起動・停止時のフック。"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.deps import build_context, close_context
from app.services.app_settings_loader import apply_db_overrides_to_env
from config.settings import get_settings
from db.migrations import run_migrations

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # まず env/デフォルトだけで settings を取得し db_file の場所を確定する。
    # create_app でも get_settings() が呼ばれているためここで一旦同じ値が返る。
    bootstrap_settings = get_settings()
    run_migrations(bootstrap_settings.db_file)
    logger.info("migrations applied: %s", bootstrap_settings.db_file)

    # DB の app_settings を os.environ に注入し、Settings を再構築する。
    # 既に create_app で get_settings() が lru_cache に詰まっているので、
    # cache_clear してから再取得することで override を反映させる。
    injected = apply_db_overrides_to_env(bootstrap_settings.db_file)
    logger.info("applied %d overrides from app_settings", injected)
    get_settings.cache_clear()
    settings = get_settings()

    context = build_context(settings)
    app.state.context = context
    logger.info(
        "context built (gateway=%s, dry_run=%s)",
        "live" if context.is_fully_configured else "none",
        settings.dry_run,
    )

    scheduler = None
    if settings.scheduler_enabled and context.is_fully_configured:
        from scheduler.runtime import start_scheduler

        scheduler = start_scheduler(context)
        app.state.scheduler = scheduler
        logger.info("scheduler started")

        # 起動直後にキャッシュを事前ウォーム化する。ブラウザからの最初の
        # ダッシュボード / 予約画面アクセスで schedule API を叩くのを避けるため、
        # バックグラウンドスレッドで非同期実行（app の起動を遅らせない）。
        import threading
        from scheduler.jobs.cache_refresh import cache_refresh_job

        def _warm() -> None:
            try:
                cache_refresh_job(context)
                logger.info("initial cache warmup done")
            except Exception as exc:  # noqa: BLE001
                logger.warning("initial cache warmup failed: %s", exc)

        threading.Thread(target=_warm, name="cache-warmup", daemon=True).start()
    else:
        logger.info("scheduler disabled (enabled=%s, configured=%s)", settings.scheduler_enabled, context.is_fully_configured)

    try:
        yield
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)
            logger.info("scheduler stopped")
        close_context(context)
