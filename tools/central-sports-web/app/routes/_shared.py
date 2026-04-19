"""ルート間で共有するヘルパ。"""

from __future__ import annotations

from pathlib import Path

from fastapi import Request

from app.domain.entities import Studio
from db.repositories import studio_repo

STUDIO_COOKIE = "csw_studio_id"


def resolve_current_studio(request: Request, db_path: Path) -> Studio | None:
    cookie = request.cookies.get(STUDIO_COOKIE)
    if cookie:
        try:
            pk = int(cookie)
        except ValueError:
            pk = None
        if pk is not None:
            studio = studio_repo.get_studio_by_id(db_path, pk)
            if studio is not None:
                return studio
    return studio_repo.get_default_studio(db_path)
