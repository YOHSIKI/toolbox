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

    def signout(self) -> dict[str, Any]:
        """POST /api/system/auth/signout"""
        r = self.http.post(f"{API_BASE}/system/auth/signout", json={}, timeout=15)
        return r.json()
