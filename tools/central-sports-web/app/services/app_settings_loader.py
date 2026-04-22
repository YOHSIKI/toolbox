"""app_settings テーブルを os.environ 経由で pydantic Settings に適用するローダ。

pydantic-settings の `env_prefix="CSW_"` を活かし、DB の各キーを
`CSW_<UPPER>` として os.environ に書き込む。pydantic は env の値を
env_file（.env）や class default よりも優先するため、これだけで
DB > .env > default の優先順位が自然に成立する。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from db.repositories import app_settings_repo

logger = logging.getLogger(__name__)


def apply_db_overrides_to_env(db_path: Path) -> int:
    """DB の app_settings を os.environ に注入する。

    返り値: 注入したキー数。DB 読み取りに失敗した場合は 0 を返し、
    起動を阻害しない（既存の env / default で動作継続）。
    """

    try:
        items = app_settings_repo.get_all(db_path)
    except Exception as exc:  # noqa: BLE001 - 起動阻害を避ける
        logger.warning(
            "app_settings override load failed (db=%s): %s", db_path, exc
        )
        return 0
    for key, value in items.items():
        env_name = f"CSW_{key.upper()}"
        os.environ[env_name] = str(value)
    return len(items)


__all__ = ["apply_db_overrides_to_env"]
