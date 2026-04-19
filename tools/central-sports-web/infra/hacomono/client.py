"""Hacomono 予約 API の薄いクライアント。

- HttpBackend を使って各エンドポイントを叩く
- `errors[].code` を HacomonoError 階層に翻訳（書き込み系）
- 失効応答を検知したら AuthSession で 1 回だけ再ログインしてリトライ
- ドメインエンティティへのマッピングは行わず、生 payload を上位層に渡す
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from infra.hacomono.auth import AuthSession, is_session_expired_response
from infra.hacomono.endpoints import (
    AUTH_DETAIL_PATH,
    CANCEL_PATH,
    MOVE_PATH,
    RESERVATIONS_PATH,
    RESERVE_PATH,
    SCHEDULE_PATH,
    list_nos_path,
    reservations_referer,
    schedule_referer,
)
from infra.hacomono.errors import (
    AlreadyReserved,
    BusinessRuleViolation,
    CapacityExceeded,
    HacomonoError,
    OutsideReservationWindow,
    SeatUnavailable,
    SessionExpired,
)
from infra.hacomono.http import HttpBackend, RawResponse, new_request_id

logger = logging.getLogger(__name__)


# errors[].code からドメイン例外へのマッピング
_ERROR_CODE_MAP: dict[str, type[HacomonoError]] = {
    "E_ALREADY_RESERVED": AlreadyReserved,
    "E_RESERVATION_ALREADY_EXISTS": AlreadyReserved,
    "E_SEAT_UNAVAILABLE": SeatUnavailable,
    "E_SEAT_TAKEN": SeatUnavailable,
    "E_NO_UNAVAILABLE": SeatUnavailable,
    "E_RESERVATION_CLOSED": OutsideReservationWindow,
    "E_RESERVATION_NOT_YET_OPEN": OutsideReservationWindow,
    "E_OUTSIDE_WINDOW": OutsideReservationWindow,
    "E_CAPACITY_EXCEEDED": CapacityExceeded,
    "E_LESSON_FULL": CapacityExceeded,
}


def _raise_for_errors(response: RawResponse) -> None:
    errors = response.payload.get("errors") if isinstance(response.payload, dict) else None
    if not isinstance(errors, list) or not errors:
        return
    first = errors[0]
    if not isinstance(first, dict):
        raise BusinessRuleViolation("unexpected errors shape", raw={"errors": errors})
    code = str(first.get("code") or "")
    message = str(first.get("message") or code or "business rule violation")
    exc_cls = _ERROR_CODE_MAP.get(code, BusinessRuleViolation)
    raise exc_cls(message, code=code, raw={"errors": errors})


class HacomonoClient:
    """各 API の 1 対 1 ラッパ。"""

    def __init__(self, http: HttpBackend, auth: AuthSession) -> None:
        self._http = http
        self._auth = auth

    # --- 読み取り系 -----------------------------------------------

    def auth_detail(self) -> RawResponse:
        return self._with_auth(lambda: self._http.get_json(AUTH_DETAIL_PATH))

    def fetch_schedule(
        self,
        *,
        studio_id: int,
        studio_room_id: int,
        date_from: str,
        date_to: str | None = None,
    ) -> RawResponse:
        query: dict[str, Any] = {
            "schedule_type": "rooms_fill",
            "studio_id": studio_id,
            "studio_room_id": studio_room_id,
            "date_from": date_from,
        }
        if date_to:
            query["date_to"] = date_to
        referer = schedule_referer(studio_id, studio_room_id)
        return self._with_auth(
            lambda: self._http.get_query_json(SCHEDULE_PATH, query, referer=referer)
        )

    def list_my_reservations(self) -> RawResponse:
        return self._with_auth(
            lambda: self._http.get_json(RESERVATIONS_PATH, referer=reservations_referer())
        )

    def list_reserved_nos(self, studio_lesson_id: int) -> RawResponse:
        path = list_nos_path(studio_lesson_id)
        return self._with_auth(lambda: self._http.get_json(path))

    # --- 書き込み系（dry-run 判定は上位層が行う） ------------------

    def reserve(
        self,
        *,
        studio_lesson_id: int,
        no: int,
        ticket_id: int | None = None,
        contract_group_no: int | None = None,
        reservation_type: str | None = None,
    ) -> RawResponse:
        body: dict[str, Any] = {"studio_lesson_id": studio_lesson_id, "no": no}
        if ticket_id is not None:
            body["ticket_id"] = ticket_id
        if contract_group_no is not None:
            body["contract_group_no"] = contract_group_no
        if reservation_type is not None:
            body["reservation_type"] = reservation_type
        return self._with_auth_and_check(
            lambda: self._http.post_json(
                RESERVE_PATH, body, referer=reservations_referer()
            )
        )

    def cancel(self, reservation_ids: list[int]) -> RawResponse:
        return self._with_auth_and_check(
            lambda: self._http.put_json(
                CANCEL_PATH,
                {"reservation_ids": reservation_ids},
                referer=reservations_referer(),
            )
        )

    def move(self, reservation_id: int, no: int) -> RawResponse:
        return self._with_auth_and_check(
            lambda: self._http.put_json(
                MOVE_PATH,
                {"reservation_id": reservation_id, "no": no},
                referer=reservations_referer(),
            )
        )

    # --- 共通ラッパ -----------------------------------------------

    def _with_auth(self, call: Callable[[], RawResponse]) -> RawResponse:
        new_request_id()
        if not self._auth.is_authenticated:
            self._auth.sign_in()
        response = call()
        if is_session_expired_response(response):
            self._auth.reauthenticate_once()
            response = call()
            if is_session_expired_response(response):
                raise SessionExpired("session still expired after reauthentication")
        return response

    def _with_auth_and_check(self, call: Callable[[], RawResponse]) -> RawResponse:
        response = self._with_auth(call)
        _raise_for_errors(response)
        return response
