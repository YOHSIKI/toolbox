"""公開月間 API の日次同期。

認証不要の `/club/jsonp_schedule.php` を各店舗 × 今月 + 翌月で 1 日 1 回叩き、
gateway 内部の `_weekday_time_space`（曜日×時刻 → studio_room_space_id）と
公開月間キャッシュを最新化する。reserve API 範囲外の週でも intent 登録時に
正しい座席レイアウトを描画するためのデータ源。

reserve API 窓（今日〜+6 日）の schedule も併せて取得して、
`_weekday_time_space` を確定値で上書きする（最新の座席レイアウト変更に追従）。
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime

from app.deps import AppContext
from app.domain.entities import (
    HistoryCategory,
    HistoryEntry,
    HistoryResult,
    StudioRef,
)
from db.repositories import history_repo, studio_repo

logger = logging.getLogger(__name__)


def monthly_sync_job(context: AppContext) -> None:
    """毎日 1 回、公開月間 API から今月 + 翌月分を取得して座席レイアウト情報を学習する。"""

    if context.gateway is None:
        logger.info("monthly_sync skipped: not configured")
        return
    today = datetime.now(tz=context.settings.tz).date()
    studios = studio_repo.list_studios(context.db_path)

    # 今月 + 翌月
    targets: list[tuple[int, int]] = [(today.year, today.month)]
    if today.month == 12:
        targets.append((today.year + 1, 1))
    else:
        targets.append((today.year, today.month + 1))

    total_public = 0
    total_reserve = 0
    errors = 0

    # Step 1: reserve API 窓（今日〜+6 日）を先に取得して _weekday_time_space を
    # 確定値で学習する。gateway 内部のキャッシュも温まる。
    for studio in studios:
        try:
            lessons = context.gateway.fetch_week(
                StudioRef(studio.studio_id, studio.studio_room_id), today
            )
            total_reserve += len(lessons)
        except Exception as exc:  # noqa: BLE001
            errors += 1
            logger.warning(
                "monthly_sync: reserve fetch_week failed studio=%d: %s",
                studio.studio_id, exc,
            )

    # Step 2: 公開月間 API で今月・翌月を取得。club_code / sisetcd がある店舗のみ。
    for studio in studios:
        if not studio.club_code or not studio.sisetcd:
            continue
        for year, month in targets:
            try:
                lessons = context.gateway.fetch_monthly_public(  # type: ignore[attr-defined]
                    club_code=studio.club_code,
                    sisetcd=studio.sisetcd,
                    studio_id=studio.studio_id,
                    studio_room_id=studio.studio_room_id,
                    year=year,
                    month=month,
                )
                total_public += len(lessons)
            except Exception as exc:  # noqa: BLE001
                errors += 1
                logger.warning(
                    "monthly_sync: public monthly fetch failed studio=%d ym=%04d%02d: %s",
                    studio.studio_id, year, month, exc,
                )

    history_repo.insert(
        context.db_path,
        HistoryEntry(
            id=None,
            request_id=uuid.uuid4().hex[:12],
            occurred_at=datetime.now(),
            category=HistoryCategory.MONTHLY_SYNC,
            endpoint="schedule.public_monthly",
            elapsed_ms=None,
            result=(
                HistoryResult.FAILURE
                if errors and total_public == 0 and total_reserve == 0
                else HistoryResult.WARNING if errors
                else HistoryResult.SUCCESS
            ),
            message=(
                f"月間同期: 公開月間 {total_public} 件 / 予約 API {total_reserve} 件"
                f"（エラー {errors} 件）"
            ),
            metadata={
                "months": [f"{y:04d}-{m:02d}" for y, m in targets],
                "studios": len(studios),
                "errors": errors,
                "public_lessons": total_public,
                "reserve_lessons": total_reserve,
            },
        ),
    )


def _first_of_next_month(today: date) -> date:
    """後方互換用。既存のテスト or 呼び出しがあれば残す。"""

    if today.month == 12:
        return date(today.year + 1, 1, 1)
    return date(today.year, today.month + 1, 1)
