"""外形監視向けのヘルスチェック。"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.deps import AppContext, get_context

router = APIRouter()


@router.get("/healthz")
def healthz(context: AppContext = Depends(get_context)) -> JSONResponse:
    db_ok = False
    try:
        connection = sqlite3.connect(str(context.db_path))
        connection.execute("SELECT 1").fetchone()
        connection.close()
        db_ok = True
    except Exception:
        db_ok = False

    return JSONResponse(
        {
            "status": "ok" if db_ok else "degraded",
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "db_ok": db_ok,
            "gateway": "configured" if context.is_fully_configured else "missing_secrets",
            "dry_run": context.settings.dry_run,
            "version": context.settings.version,
        }
    )
