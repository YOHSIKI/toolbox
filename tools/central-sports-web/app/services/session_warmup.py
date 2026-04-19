"""8:55 に走る事前ログインと対象レッスンの解決。"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from app.domain.entities import (
    HistoryCategory,
    HistoryEntry,
    HistoryResult,
    RecurringStatus,
    StudioRef,
)
from app.domain.ports import ReservationGateway
from db.repositories import history_repo, recurring_repo
from infra.notifier.discord import DiscordNotifier, NotifyLevel

logger = logging.getLogger(__name__)


class SessionWarmupService:
    def __init__(
        self,
        db_path: Path,
        gateway: ReservationGateway,
        notifier: DiscordNotifier,
    ) -> None:
        self._db_path = db_path
        self._gateway = gateway
        self._notifier = notifier

    def run(self, *, today: date) -> bool:
        request_id = uuid.uuid4().hex[:12]
        started = datetime.now()
        ok = True
        message = "事前ログイン成功、対象レッスンも確認できました"
        try:
            self._gateway.ensure_authenticated()
        except Exception as exc:  # noqa: BLE001
            ok = False
            message = f"事前ログインに失敗しました: {exc}"
            logger.error("warmup sign-in failed: %s", exc)

        targets_by_studio: dict[StudioRef, list[str]] = {}
        if ok:
            target_date = today + timedelta(days=7)
            active = [
                r
                for r in recurring_repo.list_recurring(self._db_path)
                if r.status is RecurringStatus.ACTIVE
                and r.day_of_week == target_date.weekday()
            ]
            for item in active:
                ref = StudioRef(item.studio_id, item.studio_room_id)
                targets_by_studio.setdefault(ref, []).append(
                    f"{item.start_time} {item.program_name}"
                )
            for ref in targets_by_studio:
                week_start = target_date - timedelta(days=target_date.weekday())
                try:
                    self._gateway.fetch_week(ref, week_start)
                except Exception as exc:  # noqa: BLE001
                    ok = False
                    message = f"対象スケジュールの取得に失敗しました: {exc}"
                    logger.warning("warmup schedule fetch failed: %s", exc)
                    break

        elapsed_ms = int((datetime.now() - started).total_seconds() * 1000)
        history_repo.insert(
            self._db_path,
            HistoryEntry(
                id=None,
                request_id=request_id,
                occurred_at=datetime.now(),
                category=HistoryCategory.LOGIN,
                endpoint="warmup",
                elapsed_ms=elapsed_ms,
                result=HistoryResult.SUCCESS if ok else HistoryResult.FAILURE,
                message=message,
                metadata={
                    "targets": sum(len(v) for v in targets_by_studio.values()),
                },
            ),
        )
        if ok:
            lines = []
            for ref, names in targets_by_studio.items():
                lines.append(f"店舗 {ref.studio_id} 部屋 {ref.studio_room_id}: {len(names)} 件")
                for n in names:
                    lines.append(f"  - {n}")
            description = "\n".join(lines) if lines else "本日予約対象の定期予約はありません。"
            self._notifier.send(
                title="9 時の予約実行、準備完了",
                description=description,
                level=NotifyLevel.SUCCESS,
            )
        else:
            self._notifier.send(
                title="予約の事前準備で問題が発生しました",
                description=message,
                level=NotifyLevel.DANGER,
            )
        return ok
