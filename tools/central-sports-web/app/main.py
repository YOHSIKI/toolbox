"""FastAPI アプリのエントリポイント。"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.auth import require_basic_auth
from app.lifespan import lifespan
from app.routes import (
    dashboard,
    debug,
    health,
    intents,
    recurring,
    reserve,
    studios,
    sync,
)
from app.routes import (
    settings as settings_route,
)
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

    # 全リクエストのサーバー側処理時間を計測してログとレスポンスヘッダに出す。
    # ブラウザ DevTools でも X-Process-Time-Ms / Server-Timing として確認できる。
    @app.middleware("http")
    async def timing_middleware(request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000
        # healthz はノイズになるのでログ出さない
        if request.url.path != "/healthz":
            logger.info(
                "request %s %s -> %d in %.1fms",
                request.method,
                request.url.path + (f"?{request.url.query}" if request.url.query else ""),
                response.status_code,
                elapsed_ms,
            )
        response.headers["X-Process-Time-Ms"] = f"{elapsed_ms:.1f}"
        response.headers["Server-Timing"] = f"total;dur={elapsed_ms:.1f}"
        return response

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
        settings_route.router,
        dependencies=[Depends(require_basic_auth)],
        tags=["settings"],
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
    app.include_router(
        intents.router,
        dependencies=[Depends(require_basic_auth)],
        tags=["intents"],
    )
    app.include_router(
        debug.router,
        dependencies=[Depends(require_basic_auth)],
        tags=["debug"],
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
