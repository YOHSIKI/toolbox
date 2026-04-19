"""リポジトリ共通のユーティリティ。"""

from __future__ import annotations

from datetime import datetime

ISO_FORMAT = "%Y-%m-%d %H:%M:%S"


def parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.strptime(value, ISO_FORMAT)
    except ValueError:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None


def format_datetime(dt: datetime) -> str:
    return dt.strftime(ISO_FORMAT)
