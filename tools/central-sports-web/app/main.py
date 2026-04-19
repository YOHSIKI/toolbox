"""FastAPI アプリのエントリポイント。"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.auth import require_basic_auth
from app.lifespan import lifespan
from app.routes import dashboard, health, recurring, reserve, studios, sync
from config.settings import Settings, get_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    static_dir = BASE_DIR / "ui" / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    app.include_router(health.router, tags=["health"])
    app.include_router(
        dashboard.router,
        dependencies=[Depends(require_basic_auth)],
        tags=["dashboard"],
    )
    app.include_router(
        reserve.router,
        dependencies=[Depends(require_basic_auth)],
        tags=["reserve"],
    )
    app.include_router(
        recurring.router,
        dependencies=[Depends(require_basic_auth)],
        tags=["recurring"],
    )
    app.include_router(
        studios.router,
        dependencies=[Depends(require_basic_auth)],
        tags=["studios"],
    )
    app.include_router(
        sync.router,
        dependencies=[Depends(require_basic_auth)],
        tags=["sync"],
    )

    @app.get("/meta")
    def meta(settings: Settings = Depends(get_settings)) -> JSONResponse:
        return JSONResponse(
            {
                "app": settings.app_name,
                "version": settings.version,
                "dry_run": settings.dry_run,
                "timezone": settings.timezone,
            }
        )

    return app


app = create_app()
