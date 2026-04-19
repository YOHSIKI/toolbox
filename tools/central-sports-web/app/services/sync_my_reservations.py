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
from datetime import date, datetime, time as dtime, timedelta
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
        # マイページ側でキャンセルされた予約（API から消えた既存 CONFIRMED）を検出し、
        # サマリーに反映するため reservation.cancel の履歴を追加する。履歴に残すこと
        # で dashboard の _summarize が「予約成功 → 取消」ペアとして対消滅扱いできる。
        # mark_missing_as_cancelled が今回の UPDATE で cancelled 化した external_id を
        # 返してくれるので、それを元に history を書く。これで「事前計算 → UPDATE →
        # 次回 sync」の race で history が欠落するのを防ぐ。
        cancelled_ids = reservation_repo.mark_missing_as_cancelled(
            self._db_path, active_external_ids
        )
        cancelled = len(cancelled_ids)
        for ext_id in cancelled_ids:
            r = known.get(ext_id)
            if r is None:
                continue
            self._insert_external_cancel_history(request_id, r)
        # リカバリ: 今日すでに cancelled 化されているが history に reservation.cancel
        # が残っていない予約（旧バージョンで取りこぼしたもの）を後追い記録する。
        # 同じ予約に対する重複を避けるため、today の history を metadata 単位で照合。
        self._recover_missing_cancel_history(request_id, today=started.date())
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

    def _insert_external_cancel_history(
        self, request_id: str, reservation
    ) -> None:
        """マイページ取消に伴う reservation.cancel 履歴を 1 件書く。"""

        try:
            history_repo.insert(
                self._db_path,
                HistoryEntry(
                    id=None,
                    request_id=request_id,
                    occurred_at=datetime.now(),
                    category=HistoryCategory.SYNC_MY_RESERVATIONS,
                    endpoint="reservation.cancel",
                    elapsed_ms=None,
                    result=HistoryResult.SUCCESS,
                    message=f"{reservation.program_name} の予約がマイページ側で取り消されました",
                    metadata={
                        "program_id": reservation.program_id,
                        "program_name": reservation.program_name,
                        "lesson_date": reservation.lesson_date.isoformat(),
                        "lesson_time": reservation.lesson_time,
                        "seat_no": reservation.seat_no,
                        "source": "external",
                    },
                ),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("failed to record external cancel history: %s", exc)

    def _recover_missing_cancel_history(
        self, request_id: str, *, today: date
    ) -> None:
        """今日 cancelled 化されたのに history が残っていない予約を後追い記録する。

        旧実装では `mark_missing_as_cancelled` が件数しか返さず、かつ history 記録
        より先に status が cancelled 化されていた場合に history を書けないケース
        があった。その取りこぼしを次回 sync で 1 回だけ救済する。重複防止のため
        today の history を metadata 単位で照合する。

        SQLite の `datetime('now')` は UTC、`datetime.now()` はローカル TZ（JST）
        のため、updated_at の UTC 日付と JST の today は半日以上ズレ得る。
        取りこぼしを避けるため updated_at は today ± 1 日で広めに拾い、重複は
        history 側の key 照合ではじく。
        """

        try:
            cancelled = reservation_repo.list_reservations(
                self._db_path, status=ReservationStatus.CANCELLED, since=None
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("recover: failed to list cancelled reservations: %s", exc)
            return
        window = {today, today - timedelta(days=1), today + timedelta(days=1)}
        todays_cancelled = [
            r for r in cancelled
            if r.updated_at is not None and r.updated_at.date() in window
        ]
        if not todays_cancelled:
            return
        start_of_day = datetime.combine(today, dtime(0, 0))
        end_of_day = datetime.combine(today, dtime(23, 59, 59))
        try:
            todays_history = history_repo.list_between(
                self._db_path,
                start=start_of_day,
                end=end_of_day,
                categories=[HistoryCategory.SYNC_MY_RESERVATIONS],
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("recover: failed to list today history: %s", exc)
            return
        existing_keys: set[tuple[str, str, str]] = set()
        for h in todays_history:
            if h.endpoint != "reservation.cancel":
                continue
            meta = h.metadata or {}
            existing_keys.add(
                (
                    str(meta.get("program_id", "")),
                    str(meta.get("lesson_date", "")),
                    str(meta.get("lesson_time", "")),
                )
            )
        for r in todays_cancelled:
            key = (
                str(r.program_id),
                r.lesson_date.isoformat(),
                r.lesson_time,
            )
            if key in existing_keys:
                continue
            self._insert_external_cancel_history(request_id, r)
            existing_keys.add(key)
