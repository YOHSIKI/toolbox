"""Central Sports 予約サイト (reserve.central.co.jp) の内部 API クライアント。

サイトは Nuxt SPA + Rails API（hacomono プラットフォーム）。
API は非公開だが、ブラウザ内 JS が叩くのと同じ HTTP 呼び出しをする。
"""

from __future__ import annotations

import json
import secrets as _secrets
import urllib.parse
from dataclasses import dataclass
from typing import Any

import requests

BASE_URL = "https://reserve.central.co.jp"
API_BASE = f"{BASE_URL}/api"
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def make_device_id() -> str:
    """ブラウザ相当のランダム 40 文字 hex device_id を生成する。"""
    return _secrets.token_hex(20)


@dataclass
class Session:
    """認証済みセッション。cookies（device_id + _at）を保持する。"""

    http: requests.Session
    device_id: str

    @classmethod
    def new(cls, device_id: str | None = None) -> "Session":
        s = requests.Session()
        s.headers.update({
            "User-Agent": UA,
            "Accept": "application/json",
            "Accept-Language": "ja,en;q=0.8",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": BASE_URL,
            "Referer": f"{BASE_URL}/app/login",
        })
        dev_id = device_id or make_device_id()
        s.cookies.set("device_id", dev_id, domain="reserve.central.co.jp", path="/")
        return cls(http=s, device_id=dev_id)

    def signin(self, mail_address: str, password: str) -> dict[str, Any]:
        """POST /api/system/auth/signin

        成功時: Set-Cookie `_at=<access_token>` が入り、以降の呼び出しで認証される。
        失敗時: errors に AUT_000002 等が入る。呼び出し側で判定する。
        """
        r = self.http.post(
            f"{API_BASE}/system/auth/signin",
            json={"mail_address": mail_address, "password": password},
            timeout=15,
        )
        return r.json()

    def _get_query(self, path: str, query: dict[str, Any]) -> dict[str, Any]:
        """query を JSON-encode して ?query= に載せて GET。

        これはサイトの JS が使っている axios paramsSerializer の挙動に合わせた形式。
        """
        q = urllib.parse.quote(json.dumps(query, ensure_ascii=False))
        r = self.http.get(f"{API_BASE}{path}?query={q}", timeout=15)
        return r.json()

    def get_schedule(
        self,
        studio_id: int,
        studio_room_id: int,
        date_from: str,
        date_to: str | None = None,
        schedule_type: str = "rooms_fill",
    ) -> dict[str, Any]:
        """GET /api/master/studio-lessons/schedule

        date_from / date_to は "YYYY-MM-DD"。date_to 省略時は date_from から一週間が既定挙動。
        """
        query: dict[str, Any] = {
            "schedule_type": schedule_type,
            "studio_id": studio_id,
            "studio_room_id": studio_room_id,
            "date_from": date_from,
        }
        if date_to:
            query["date_to"] = date_to
        return self._get_query("/master/studio-lessons/schedule", query)

    def get_auth_detail(self) -> dict[str, Any]:
        """GET /api/system/auth/detail — 認証後の自分の会員情報。"""
        r = self.http.get(f"{API_BASE}/system/auth/detail", timeout=15)
        return r.json()

    def get_reserve_context(
        self,
        studio_lesson_id: int,
        no: int | None = None,
        ticket_id: int | None = None,
        contract_group_no: int | None = None,
    ) -> dict[str, Any]:
        """GET /api/reservation/reservations/context

        特定のレッスン 1 件に対する予約状況を返す。studio_lesson_id だけで呼べる。
        レスポンスに reserve_processions / reservation_priorities / ticket 情報が入る。
        """
        query: dict[str, Any] = {"studio_lesson_id": studio_lesson_id}
        if no is not None:
            query["no"] = no
        if ticket_id is not None:
            query["ticket_id"] = ticket_id
        if contract_group_no is not None:
            query["contract_group_no"] = contract_group_no
        return self._get_query("/reservation/reservations/context", query)

    def signout(self) -> dict[str, Any]:
        """POST /api/system/auth/signout"""
        r = self.http.post(f"{API_BASE}/system/auth/signout", json={}, timeout=15)
        return r.json()

    def list_nos(self, studio_lesson_id: int) -> dict[str, Any]:
        """GET /api/reservation/reservations/{lesson_id}/no

        該当 lesson で**予約済み** の no リスト（マイページ画面でグレー表示の座席番号）。
        空き数は studio_room_space の space_num から引いて求める。
        """
        r = self.http.get(
            f"{API_BASE}/reservation/reservations/{studio_lesson_id}/no",
            timeout=15,
        )
        return r.json()

    def list_my_reservations(self) -> dict[str, Any]:
        """GET /api/reservation/reservations — 自分の予約一覧（現在/今後）"""
        r = self.http.get(f"{API_BASE}/reservation/reservations", timeout=15)
        return r.json()

    def cancel(self, reservation_ids: list[int]) -> dict[str, Any]:
        """PUT /api/reservation/reservations/cancel

        body: {reservation_ids: [id1, id2, ...]}
        """
        r = self.http.put(
            f"{API_BASE}/reservation/reservations/cancel",
            json={"reservation_ids": reservation_ids},
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
        """POST /api/reservation/reservations/reserve

        body: {studio_lesson_id, no, [ticket_id], [contract_group_no], [reservation_type], ...}
        成功時 data.reservation に予約結果が入る。
        """
        body: dict[str, Any] = {
            "studio_lesson_id": studio_lesson_id,
            "no": no,
        }
        if ticket_id is not None:
            body["ticket_id"] = ticket_id
        if contract_group_no is not None:
            body["contract_group_no"] = contract_group_no
        if reservation_type is not None:
            body["reservation_type"] = reservation_type
        r = self.http.post(
            f"{API_BASE}/reservation/reservations/reserve",
            json=body,
            timeout=20,
        )
        return r.json()
