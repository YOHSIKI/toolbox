"""hacomono の自分の予約一覧をローカル DB と同期する。

- 実 API から取得したレコードを `reservations` テーブルに UPSERT
- ローカルにあって API に無いものは `cancelled` に更新
- 定期予約由来かどうかは、既存の origin 値が判明していればそれを優先
- UI からの呼び出しは `run_if_stale` で TTL 付き、スケジューラからは `run` を直接
- 実行結果は `history` テーブルに残す（UI の履歴やダッシュボードから追える）
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.domain.entities import (
    HistoryCategory,
    HistoryEntry,
    HistoryResult,
    ReservationOrigin,
    ReservationStatus,
)
from app.domain.ports import ReservationGateway
from db.repositories import history_repo, reservation_repo

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SyncResult:
    fetched: int
    inserted_or_updated: int
    cancelled_locally: int


class SyncMyReservationsService:
    """自分の予約一覧をローカルと同期する。UI 経由では TTL で間引く。"""

    DEFAULT_INTERVAL_SEC = 86400.0  # 24 時間（1 日に 1 回）

    def __init__(self, db_path: Path, gateway: ReservationGateway) -> None:
        self._db_path = db_path
        self._gateway = gateway
        self._last_sync_ts: float = 0.0
        self._lock = threading.Lock()

    def run_if_stale(
        self, interval_seconds: float | None = None
    ) -> SyncResult | None:
        """前回同期から interval 秒経過していれば同期を実行。"""

        interval = interval_seconds if interval_seconds is not None else self.DEFAULT_INTERVAL_SEC
        now = time.monotonic()
        if now - self._last_sync_ts < interval:
            return None
        # 同時呼び出しは 1 本にまとめる（他が走っていれば待たず skip）
        if not self._lock.acquire(blocking=False):
            return None
        try:
            result = self.run()
            self._last_sync_ts = time.monotonic()
            return result
        except Exception as exc:  # noqa: BLE001 - UI を落とさない
            logger.warning("sync my reservations failed: %s", exc)
            # 失敗時は TTL を進めず、次回再試行させる
            return None
        finally:
            self._lock.release()

    def run(self) -> SyncResult:
        request_id = uuid.uuid4().hex[:12]
        started = datetime.now()
        try:
            remote = self._gateway.fetch_my_reservations()
        except Exception as exc:  # noqa: BLE001
            elapsed_ms = int((datetime.now() - started).total_seconds() * 1000)
            self._record_history(
                request_id,
                HistoryResult.FAILURE,
                f"予約一覧の取得に失敗しました: {exc}",
                elapsed_ms=elapsed_ms,
                metadata={"error": type(exc).__name__},
            )
            raise

        known = {
            r.external_id: r
            for r in reservation_repo.list_reservations(
                self._db_path, status=None
            )
            if r.external_id is not None
        }
        updated = 0
        active_external_ids: list[int] = []
        for reservation in remote:
            if reservation.external_id is None:
                continue
            active_external_ids.append(reservation.external_id)
            prior = known.get(reservation.external_id)
            if prior is not None:
                reservation.id = prior.id
                reservation.origin = prior.origin
                reservation.origin_id = prior.origin_id
            else:
                reservation.id = uuid.uuid4().hex
                reservation.origin = ReservationOrigin.SINGLE
                reservation.origin_id = None
            reservation.status = ReservationStatus.CONFIRMED
            reservation_repo.upsert_reservation(self._db_path, reservation)
            updated += 1
        cancelled = reservation_repo.mark_missing_as_cancelled(
            self._db_path, active_external_ids
        )
        elapsed_ms = int((datetime.now() - started).total_seconds() * 1000)
        logger.info(
            "sync reservations: fetched=%d updated=%d cancelled=%d elapsed=%dms",
            len(remote), updated, cancelled, elapsed_ms,
        )
        # サンプルを INFO ログに出して、マッピング結果を追える状態にする
        for sample in remote[:3]:
            logger.info(
                "  sample ext=%s lesson=%s %s program=%s seat=%s studio=%s/%s",
                sample.external_id, sample.lesson_date, sample.lesson_time,
                sample.program_name, sample.seat_no,
                sample.studio_id, sample.studio_room_id,
            )
        self._record_history(
            request_id,
            HistoryResult.SUCCESS,
            f"マイページから {len(remote)} 件の予約を同期（新規・更新 {updated} 件、取消扱い {cancelled} 件）",
            elapsed_ms=elapsed_ms,
            metadata={
                "fetched": len(remote),
                "updated": updated,
                "cancelled": cancelled,
            },
        )
        return SyncResult(
            fetched=len(remote),
            inserted_or_updated=updated,
            cancelled_locally=cancelled,
        )

    def _record_history(
        self,
        request_id: str,
        result: HistoryResult,
        message: str,
        *,
        elapsed_ms: int | None = None,
        metadata: dict | None = None,
    ) -> None:
        try:
            history_repo.insert(
                self._db_path,
                HistoryEntry(
                    id=None,
                    request_id=request_id,
                    occurred_at=datetime.now(),
                    category=HistoryCategory.SYNC_MY_RESERVATIONS,
                    endpoint="reservation.list",
                    elapsed_ms=elapsed_ms,
                    result=result,
                    message=message,
                    metadata=metadata,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("failed to record sync history: %s", exc)
