"""1 日 1 回（00:05）reserve API 窓と公開月間 API を fetch してキャッシュを温め直すジョブ。

`HacomonoGateway` の `_cache_ttl` は 24 時間に設定されている。
ブラウザからのアクセス時点で cache が切れていると体感で 300-500ms の
空白が発生するため、深夜の定時に fetch_week / fetch_monthly_public を
呼び出して常にキャッシュがウォームな状態を維持する。

朝 9:00 の新規解放分は `auto_booking`（run_at_nine_job）が finally で
このジョブを呼ぶので、日中の時間割更新はそちらに任せる。
"""

from __future__ import annotations

import logging
from datetime import datetime

from app.deps import AppContext
from app.domain.entities import StudioRef
from db.repositories import studio_repo

logger = logging.getLogger(__name__)


def cache_refresh_job(context: AppContext) -> None:
    """キャッシュを温め直す。ブラウザアクセス時の cache miss を防ぐ。

    以下を全店舗について実行:
      - reserve API の今日〜+6 日（予約画面・ダッシュボードどちらも使う）
      - 公開月間 API の今月 + 翌月（ダッシュボードの program_changes 計算で使う）
    """

    if context.gateway is None:
        return
    today = datetime.now(tz=context.settings.tz).date()
    studios = studio_repo.list_studios(context.db_path)
    # 今月 + 翌月
    targets: list[tuple[int, int]] = [(today.year, today.month)]
    if today.month == 12:
        targets.append((today.year + 1, 1))
    else:
        targets.append((today.year, today.month + 1))

    for studio in studios:
        # reserve API 窓
        try:
            context.gateway.fetch_week(
                StudioRef(studio.studio_id, studio.studio_room_id), today
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "cache_refresh reserve failed studio=%d: %s", studio.studio_id, exc
            )
        # 公開月間 API（dashboard の program_changes が使う）
        if not studio.club_code or not studio.sisetcd:
            continue
        for year, month in targets:
            try:
                context.gateway.fetch_monthly_public(  # type: ignore[attr-defined]
                    club_code=studio.club_code,
                    sisetcd=studio.sisetcd,
                    studio_id=studio.studio_id,
                    studio_room_id=studio.studio_room_id,
                    year=year,
                    month=month,
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "cache_refresh monthly failed studio=%d ym=%04d%02d: %s",
                    studio.studio_id, year, month, exc,
                )
