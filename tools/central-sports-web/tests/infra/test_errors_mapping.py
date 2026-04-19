from __future__ import annotations

import pytest

from infra.hacomono.client import _raise_for_errors
from infra.hacomono.errors import (
    AlreadyReserved,
    BusinessRuleViolation,
    CapacityExceeded,
    OutsideReservationWindow,
    SeatUnavailable,
)
from infra.hacomono.http import RawResponse


def _resp(errors: list | None) -> RawResponse:
    payload = {"errors": errors} if errors is not None else {}
    return RawResponse(status_code=200, payload=payload, elapsed_ms=10, request_id="rid")


def test_no_errors_returns_none() -> None:
    _raise_for_errors(_resp(None))
    _raise_for_errors(_resp([]))


@pytest.mark.parametrize(
    "code,exc_cls",
    [
        ("E_ALREADY_RESERVED", AlreadyReserved),
        ("E_RESERVATION_ALREADY_EXISTS", AlreadyReserved),
        ("E_SEAT_UNAVAILABLE", SeatUnavailable),
        ("E_SEAT_TAKEN", SeatUnavailable),
        ("E_RESERVATION_CLOSED", OutsideReservationWindow),
        ("E_RESERVATION_NOT_YET_OPEN", OutsideReservationWindow),
        ("E_CAPACITY_EXCEEDED", CapacityExceeded),
        ("E_LESSON_FULL", CapacityExceeded),
    ],
)
def test_known_codes_translated(code: str, exc_cls: type) -> None:
    with pytest.raises(exc_cls):
        _raise_for_errors(_resp([{"code": code, "message": "x"}]))


def test_unknown_code_falls_back_to_business_rule() -> None:
    with pytest.raises(BusinessRuleViolation):
        _raise_for_errors(_resp([{"code": "E_UNKNOWN", "message": "x"}]))
