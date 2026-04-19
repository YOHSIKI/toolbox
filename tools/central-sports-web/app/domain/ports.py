"""ドメイン側が要求する外部境界（ポート）。

具象は `app/adapters/` 以下で実装する。
"""

from __future__ import annotations

from datetime import date
from typing import Protocol

from app.domain.entities import (
    Lesson,
    Reservation,
    ReservationAttempt,
    SeatMap,
    StudioRef,
)


class ReservationGateway(Protocol):
    """hacomono 予約 API をドメイン語彙で表現したポート。"""

    def ensure_authenticated(self) -> None:
        """必要に応じてログインを試みる。既にログイン済みなら何もしない。"""

    def fetch_week(
        self,
        studio: StudioRef,
        week_start: date,
        days: int = 7,
    ) -> list[Lesson]:
        """指定店舗・指定日から N 日分のレッスンを取得する。"""

    def fetch_my_reservations(self) -> list[Reservation]:
        """自分の予約一覧を取得する（external_id 付き）。"""

    def fetch_seat_map(
        self,
        studio_lesson_id: int,
        *,
        capacity_hint: int | None = None,
        studio_room_space_id: int | None = None,
    ) -> SeatMap:
        """指定レッスンの座席埋まり状況と配置を取得する。

        - `capacity_hint` は schedule API 経由で既に分かっている定員のヒント
        - `studio_room_space_id` を渡すと、space_details から `positions` を復元する
          （空き席の飛び番号や coord_x/coord_y を UI に反映するため）
        """

    def attempt_reservation(
        self,
        studio_lesson_id: int,
        no_preferences: list[int],
    ) -> ReservationAttempt:
        """希望席の順に予約 POST を試みる。dry-run 時は擬似応答を返す。"""

    def cancel_reservation(self, external_id: int) -> None:
        """予約を取り消す。dry-run 時は擬似的に成功を返す。"""

    def change_seat(self, external_id: int, new_no: int) -> None:
        """予約の席を変更する。dry-run 時は擬似的に成功を返す。"""
