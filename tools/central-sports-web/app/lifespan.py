"""起動・停止時のフック。"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.deps import build_context, close_context
from config.settings import get_settings
from db.migrations import run_migrations

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    run_migrations(settings.db_file)
    logger.info("migrations applied: %s", settings.db_file)

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
    else:
        logger.info("scheduler disabled (enabled=%s, configured=%s)", settings.scheduler_enabled, context.is_fully_configured)

    try:
        yield
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)
            logger.info("scheduler stopped")
        close_context(context)
