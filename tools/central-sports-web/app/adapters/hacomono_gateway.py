"""`ReservationGateway` の実装。

- `HacomonoClient` を注入で受け取り、生レスポンスをドメインエンティティに変換
- dry-run モード時は書き込み系 API（reserve/cancel/move）を擬似成功に差し替え
- 予約試行は希望席の第 1 候補から順に投げ、`SeatUnavailable` なら次を試す
- `AlreadyReserved` / `CapacityExceeded` / `OutsideReservationWindow` は失敗として即時終了
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from app.adapters.public_monthly_mapper import map_public_monthly
from app.adapters.schedule_mapper import (
    map_my_reservations,
    map_reservation_result,
    map_reserved_nos,
    map_weekly_schedule,
)
from app.domain.entities import (
    Lesson,
    Reservation,
    ReservationAttempt,
    SeatMap,
    StudioRef,
)
from infra.hacomono.auth import AuthSession
from infra.hacomono.client import HacomonoClient
from infra.hacomono.errors import (
    AlreadyReserved,
    BusinessRuleViolation,
    CapacityExceeded,
    HacomonoError,
    OutsideReservationWindow,
    SeatUnavailable,
)
from infra.hacomono.public_monthly import PublicMonthlyClient

logger = logging.getLogger(__name__)


class HacomonoGateway:
    """ReservationGateway の本番実装。"""

    def __init__(
        self,
        *,
        client: HacomonoClient,
        auth: AuthSession,
        dry_run: bool,
        public_client: PublicMonthlyClient | None = None,
    ) -> None:
        self._client = client
        self._auth = auth
        self._dry_run = dry_run
        self._public_client = public_client

    @property
    def dry_run(self) -> bool:
        return self._dry_run

    # --- 認証 -----------------------------------------------

    def ensure_authenticated(self) -> None:
        if not self._auth.is_authenticated:
            self._auth.sign_in()

    # --- 読み取り系（常に実 API） --------------------------

    def fetch_week(
        self,
        studio: StudioRef,
        week_start: date,
        days: int = 7,
    ) -> list[Lesson]:
        date_from = week_start.isoformat()
        date_to = (week_start + timedelta(days=max(days, 1) - 1)).isoformat()
        response = self._client.fetch_schedule(
            studio_id=studio.studio_id,
            studio_room_id=studio.studio_room_id,
            date_from=date_from,
            date_to=date_to,
        )
        return map_weekly_schedule(
            response.payload,
            studio_id=studio.studio_id,
            studio_room_id=studio.studio_room_id,
        )

    def fetch_monthly_public(
        self,
        *,
        club_code: str,
        sisetcd: str,
        studio_id: int,
        studio_room_id: int,
        year: int,
        month: int,
        week_range: tuple[date, date] | None = None,
    ) -> list[Lesson]:
        """公開月間 API から指定月のスケジュールを取得して Lesson のリストに変換。"""

        if self._public_client is None:
            logger.warning("fetch_monthly_public: public_client is None")
            return []
        yyyymm = f"{year:04d}{month:02d}"
        logger.info("fetch_monthly_public: calling fetch(club=%s, ym=%s)", club_code, yyyymm)
        payload = self._public_client.fetch(club_code=club_code, year_month=yyyymm)
        logger.info(
            "fetch_monthly_public: payload schedule=%d closed=%d",
            len(payload.schedule), len(payload.closed_days),
        )
        mapped = map_public_monthly(
            payload,
            year=year,
            month=month,
            sisetcd=sisetcd,
            studio_id=studio_id,
            studio_room_id=studio_room_id,
            week_range=week_range,
        )
        logger.info("fetch_monthly_public: mapped %d lessons (sisetcd=%s)", len(mapped), sisetcd)
        return mapped

    def fetch_my_reservations(self) -> list[Reservation]:
        response = self._client.list_my_reservations()
        payload = response.payload
        if isinstance(payload, dict):
            top_keys = sorted(payload.keys())
            data = payload.get("data")
            data_type = type(data).__name__
            data_summary: dict[str, str] = {}
            if isinstance(data, dict):
                for k, v in data.items():
                    if isinstance(v, list):
                        preview = str(v[:3])[:300]
                        data_summary[k] = f"list(len={len(v)}) {preview}"
                    elif isinstance(v, dict):
                        data_summary[k] = f"dict(keys={sorted(v.keys())[:12]})"
                    else:
                        data_summary[k] = f"{type(v).__name__}={str(v)[:60]}"
            logger.info(
                "list_reservations top=%s data_type=%s summary=%s",
                top_keys, data_type, data_summary,
            )
        results = map_my_reservations(payload)
        logger.info("list_reservations parsed %d reservations", len(results))
        return results

    def fetch_seat_map(
        self,
        studio_lesson_id: int,
        capacity_hint: int | None = None,
    ) -> SeatMap:
        response = self._client.list_reserved_nos(studio_lesson_id)
        taken = map_reserved_nos(response.payload)
        capacity = capacity_hint or (max(taken) if taken else 0)
        return SeatMap(
            studio_lesson_id=studio_lesson_id,
            capacity=capacity,
            taken_nos=sorted(set(taken)),
        )

    # --- 書き込み系（dry-run 時は擬似応答） ----------------

    def attempt_reservation(
        self,
        studio_lesson_id: int,
        no_preferences: list[int],
    ) -> ReservationAttempt:
        if not no_preferences:
            return ReservationAttempt(
                ok=False,
                studio_lesson_id=studio_lesson_id,
                attempted_preferences=[],
                message="希望席が未指定です",
                failure_reason="NoPreferences",
            )
        if self._dry_run:
            return self._attempt_dry_run(studio_lesson_id, no_preferences)
        return self._attempt_live(studio_lesson_id, no_preferences)

    def cancel_reservation(self, external_id: int) -> None:
        if self._dry_run:
            logger.info("dry-run: skip cancel external_id=%d", external_id)
            return
        try:
            self._client.cancel([external_id])
        except BusinessRuleViolation as exc:
            logger.warning("cancel rejected by server: %s", exc)
            raise

    def change_seat(self, external_id: int, new_no: int) -> None:
        if self._dry_run:
            logger.info("dry-run: skip move external_id=%d new_no=%d", external_id, new_no)
            return
        self._client.move(reservation_id=external_id, no=new_no)

    # --- 内部 --------------------------------------------

    def _attempt_dry_run(
        self,
        studio_lesson_id: int,
        no_preferences: list[int],
    ) -> ReservationAttempt:
        """dry-run: 第 1 希望を成功扱い、実際には POST しない。"""

        pick = no_preferences[0]
        fake_id = abs(hash(("dry", studio_lesson_id, pick))) % 10**9
        return ReservationAttempt(
            ok=True,
            studio_lesson_id=studio_lesson_id,
            attempted_preferences=list(no_preferences),
            seat_no=pick,
            external_id=fake_id,
            message="dry-run のため実際の予約 POST は送っていません",
        )

    def _attempt_live(
        self,
        studio_lesson_id: int,
        no_preferences: list[int],
    ) -> ReservationAttempt:
        last_reason = "SeatUnavailable"
        last_message = "希望席がすべて埋まっていました"
        for no in no_preferences:
            try:
                response = self._client.reserve(
                    studio_lesson_id=studio_lesson_id,
                    no=no,
                )
            except SeatUnavailable as exc:
                last_reason = "SeatUnavailable"
                last_message = str(exc) or "席が取れませんでした"
                continue
            except AlreadyReserved as exc:
                return ReservationAttempt(
                    ok=False,
                    studio_lesson_id=studio_lesson_id,
                    attempted_preferences=list(no_preferences),
                    message=str(exc) or "既に予約済みでした",
                    failure_reason="AlreadyReserved",
                )
            except CapacityExceeded as exc:
                return ReservationAttempt(
                    ok=False,
                    studio_lesson_id=studio_lesson_id,
                    attempted_preferences=list(no_preferences),
                    message=str(exc) or "レッスンが満席でした",
                    failure_reason="CapacityExceeded",
                )
            except OutsideReservationWindow as exc:
                return ReservationAttempt(
                    ok=False,
                    studio_lesson_id=studio_lesson_id,
                    attempted_preferences=list(no_preferences),
                    message=str(exc) or "予約可能な時間帯外でした",
                    failure_reason="OutsideReservationWindow",
                )
            except HacomonoError as exc:
                return ReservationAttempt(
                    ok=False,
                    studio_lesson_id=studio_lesson_id,
                    attempted_preferences=list(no_preferences),
                    message=str(exc),
                    failure_reason=type(exc).__name__,
                )
            return map_reservation_result(
                response.payload,
                studio_lesson_id=studio_lesson_id,
                attempted_preferences=no_preferences,
            )
        return ReservationAttempt(
            ok=False,
            studio_lesson_id=studio_lesson_id,
            attempted_preferences=list(no_preferences),
            message=last_message,
            failure_reason=last_reason,
        )
