"""Central Sports 予約サイト (reserve.central.co.jp) の内部 API クライアント。

サイトは Nuxt SPA + Rails API（hacomono プラットフォーム）。
bot 検知回避のため:
  - **curl_cffi で Chrome 131 の TLS 指紋を偽装**（JA3/JA4 対策）
  - sec-ch-ua / sec-fetch-* / Accept-* など Chrome が送る標準ヘッダを完備
  - XHR（API 呼び出し）は `sec-fetch-mode: cors` 等、Nuxt JS と同じ値を使う
"""

from __future__ import annotations

import json
import secrets as _secrets
import urllib.parse
from dataclasses import dataclass
from typing import Any

from curl_cffi import requests as ccreq

BASE_URL = "https://reserve.central.co.jp"
API_BASE = f"{BASE_URL}/api"

# UA は impersonate プロファイル（chrome131）に curl_cffi 側が自動で合わせる。
# 下は手動で header を書くときのフォールバック。市場-platform と同じ文字列。
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# curl_cffi の impersonate プロファイル（Chrome 131 の TLS/HTTP2 指紋を真似る）
IMPERSONATE = "chrome131"

# XHR（fetch/axios）相当のリクエストで Chrome が送る標準ヘッダセット
_XHR_HEADERS: dict[str, str] = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ja,en-US;q=0.7,en;q=0.3",
    "Accept-Encoding": "gzip, deflate, br",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": BASE_URL,
    "Referer": f"{BASE_URL}/",
    "sec-ch-ua": '"Chromium";v="131", "Not_A Brand";v="24", "Google Chrome";v="131"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
}


def make_device_id() -> str:
    """ブラウザ相当のランダム 40 文字 hex device_id を生成する。"""
    return _secrets.token_hex(20)


@dataclass
class Session:
    """認証済みセッション（cookies: device_id + _at）。

    curl_cffi Session で Chrome の TLS 指紋を偽装、加えて全 Chrome XHR ヘッダを送る。
    """

    http: Any  # curl_cffi.requests.Session
    device_id: str

    @classmethod
    def new(cls, device_id: str | None = None) -> "Session":
        s = ccreq.Session(impersonate=IMPERSONATE)
        s.headers.update(_XHR_HEADERS)
        dev_id = device_id or make_device_id()
        s.cookies.set("device_id", dev_id, domain="reserve.central.co.jp", path="/")
        return cls(http=s, device_id=dev_id)

    def _post(self, path: str, body: dict[str, Any] | None, referer: str | None = None) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if referer:
            headers["Referer"] = referer
        r = self.http.post(f"{API_BASE}{path}", json=body or {}, headers=headers, timeout=15)
        return r.json()

    def _put(self, path: str, body: dict[str, Any], referer: str | None = None) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if referer:
            headers["Referer"] = referer
        r = self.http.put(f"{API_BASE}{path}", json=body, headers=headers, timeout=15)
        return r.json()

    def _get_query(self, path: str, query: dict[str, Any], referer: str | None = None) -> dict[str, Any]:
        """ブラウザ内 JS と同じ ?query=<URL-encoded JSON> 形式で GET。"""
        q = urllib.parse.quote(json.dumps(query, ensure_ascii=False))
        headers = {}
        if referer:
            headers["Referer"] = referer
        r = self.http.get(f"{API_BASE}{path}?query={q}", headers=headers or None, timeout=15)
        return r.json()

    def _get(self, path: str, referer: str | None = None) -> dict[str, Any]:
        headers = {}
        if referer:
            headers["Referer"] = referer
        r = self.http.get(f"{API_BASE}{path}", headers=headers or None, timeout=15)
        return r.json()

    # ---- Auth ----

    def signin(self, mail_address: str, password: str) -> dict[str, Any]:
        """POST /api/system/auth/signin"""
        return self._post(
            "/system/auth/signin",
            {"mail_address": mail_address, "password": password},
            referer=f"{BASE_URL}/app/login",
        )

    def signout(self) -> dict[str, Any]:
        """POST /api/system/auth/signout"""
        return self._post("/system/auth/signout", {})

    def get_auth_detail(self) -> dict[str, Any]:
        """GET /api/system/auth/detail"""
        return self._get("/system/auth/detail")

    # ---- Schedule ----

    def get_schedule(
        self,
        studio_id: int,
        studio_room_id: int,
        date_from: str,
        date_to: str | None = None,
        schedule_type: str = "rooms_fill",
    ) -> dict[str, Any]:
        """GET /api/master/studio-lessons/schedule"""
        query: dict[str, Any] = {
            "schedule_type": schedule_type,
            "studio_id": studio_id,
            "studio_room_id": studio_room_id,
            "date_from": date_from,
        }
        if date_to:
            query["date_to"] = date_to
        return self._get_query(
            "/master/studio-lessons/schedule",
            query,
            referer=f"{BASE_URL}/reserve/schedule/{studio_id}/{studio_room_id}/",
        )

    # ---- Reservation ----

    def list_my_reservations(self) -> dict[str, Any]:
        """GET /api/reservation/reservations — 自分の予約一覧"""
        return self._get("/reservation/reservations")

    def list_nos(self, studio_lesson_id: int) -> dict[str, Any]:
        """GET /api/reservation/reservations/{lesson_id}/no — 予約済み no のリスト"""
        r = self.http.get(
            f"{API_BASE}/reservation/reservations/{studio_lesson_id}/no",
            timeout=15,
        )
        return r.json()

    def reserve(
        self,
        studio_lesson_id: int,
        no: int,
        ticket_id: int | None = None,
        contract_group_no: int | None = None,
        reservation_type: str | None = None,
    ) -> dict[str, Any]:
        """POST /api/reservation/reservations/reserve"""
        body: dict[str, Any] = {"studio_lesson_id": studio_lesson_id, "no": no}
        if ticket_id is not None:
            body["ticket_id"] = ticket_id
        if contract_group_no is not None:
            body["contract_group_no"] = contract_group_no
        if reservation_type is not None:
            body["reservation_type"] = reservation_type
        return self._post("/reservation/reservations/reserve", body)

    def cancel(self, reservation_ids: list[int]) -> dict[str, Any]:
        """PUT /api/reservation/reservations/cancel"""
        return self._put("/reservation/reservations/cancel", {"reservation_ids": reservation_ids})

    def move(self, reservation_id: int, no: int) -> dict[str, Any]:
        """PUT /api/reservation/reservations/move — 席変更"""
        return self._put(
            "/reservation/reservations/move",
            {"reservation_id": reservation_id, "no": no},
        )
