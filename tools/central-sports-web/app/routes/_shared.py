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


def parse_seat_preferences(raw: str) -> list[int]:
    """フォーム入力 "5,12,1" 形式を優先順リストに変換する。

    - 全角カンマ（、）も許容
    - 空白トークンはスキップ
    - 非数値トークンはスキップ
    - 重複は順序を保って除去
    """

    raw = (raw or "").replace("、", ",")
    seats: list[int] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            seats.append(int(token))
        except ValueError:
            continue
    seen: set[int] = set()
    deduped: list[int] = []
    for s in seats:
        if s in seen:
            continue
        seen.add(s)
        deduped.append(s)
    return deduped
