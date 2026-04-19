"""公開月間スケジュール (`www.central.co.jp/club/jsonp_schedule.php`) クライアント。

JSONP（`({...});` 形式）で月間の週間パターンを返す。予約 API の 7 日制限を超えて
先の週を表示するためのソース。予約自体はこちら経由ではできない（閲覧用）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from curl_cffi import requests as ccreq

from infra.hacomono.errors import ProtocolViolation, UpstreamUnavailable
from infra.hacomono.http import CHROME_IMPERSONATE, CHROME_XHR_HEADERS

logger = logging.getLogger(__name__)

BASE_URL = "https://www.central.co.jp"


@dataclass(slots=True)
class PublicMonthlyPayload:
    """月間 JSONP のパース結果。"""

    club_name: str | None
    facilities: list[dict[str, Any]]  # pims_facility
    closed_days: list[dict[str, Any]]  # pims_closed
    schedule: list[dict[str, Any]]  # pims_schedule（週間パターン）


def _strip_jsonp(text: str) -> str:
    """`({...});` や `callback({...});` 形式の外側を剥がす。"""

    body = text.strip()
    # callback 関数名がある場合
    if "(" in body and body.endswith(")"):
        lparen = body.index("(")
        body = body[lparen + 1 : -1]
    elif body.startswith("(") and body.rstrip(";").rstrip().endswith(")"):
        body = body.rstrip()
        if body.endswith(";"):
            body = body[:-1].rstrip()
        body = body[1:-1]
    return body.strip()


class PublicMonthlyClient:
    """月間スケジュール JSONP をフェッチする薄いクライアント。"""

    def __init__(self, *, timeout: float = 10.0) -> None:
        self._timeout = timeout
        # reserve API と別ホストなので独立したセッションを使う
        self._session = ccreq.Session(impersonate=CHROME_IMPERSONATE)
        self._session.headers.update(CHROME_XHR_HEADERS)
        # 公開ページ向けの Referer に上書き
        self._session.headers["Referer"] = f"{BASE_URL}/club/schedule_detail.html"
        self._session.headers["Origin"] = BASE_URL

    def close(self) -> None:
        try:
            self._session.close()
        except Exception:  # pragma: no cover
            pass

    def fetch(self, *, club_code: str, year_month: str) -> PublicMonthlyPayload:
        """指定クラブ・年月 (YYYYMM) の JSONP を取得して dict に変換する。"""

        url = f"{BASE_URL}/club/jsonp_schedule.php"
        try:
            response = self._session.get(
                url,
                params={"club_code": club_code, "yyyymm": year_month},
                timeout=self._timeout,
            )
        except Exception as exc:
            raise UpstreamUnavailable(
                f"public monthly fetch failed: {type(exc).__name__}"
            ) from exc
        if response.status_code != 200:
            raise UpstreamUnavailable(
                f"public monthly returned {response.status_code}",
                raw={"status": response.status_code, "text": response.text[:200]},
            )
        text = response.text
        body = _strip_jsonp(text)
        try:
            payload: dict[str, Any] = json.loads(body)
        except json.JSONDecodeError as exc:
            logger.warning("public monthly JSON decode failed: head=%r", text[:200])
            raise ProtocolViolation(
                "public monthly response is not valid JSON",
                raw={"text": text[:200]},
            ) from exc

        schedule = list(payload.get("pims_schedule") or [])
        closed = list(payload.get("pims_closed") or [])
        logger.info(
            "public monthly fetched club=%s ym=%s text_len=%d schedule_rows=%d closed_rows=%d club_name=%s",
            club_code, year_month, len(text), len(schedule), len(closed),
            payload.get("club_name"),
        )
        return PublicMonthlyPayload(
            club_name=payload.get("club_name"),
            facilities=list(payload.get("pims_facility") or []),
            closed_days=closed,
            schedule=schedule,
        )
