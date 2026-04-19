"""curl_cffi をラップした HTTP バックエンド（Bot 偽装の中核）。

- Chrome 131 の TLS/HTTP2 指紋を偽装（impersonate="chrome131"）
- Chrome が送る標準 XHR ヘッダセットを全リクエストに付与
- device_id Cookie を起動時に固定（初回訪問時の値を永続化・再利用）
- Referer は呼び出し側が明示的に指定（ログイン/スケジュール/予約で異なるため）
- request_id を contextvar で引き回し、ログと構造化履歴を相関付け可能にする
"""

from __future__ import annotations

import json
import logging
import time
import urllib.parse
import uuid
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from curl_cffi import requests as ccreq

from infra.hacomono.endpoints import API_BASE, BASE_URL
from infra.hacomono.errors import (
    ProtocolViolation,
    UpstreamUnavailable,
    ValidationError,
)

logger = logging.getLogger(__name__)

# --- Bot 偽装プロファイル（最重要） -----------------------------------

CHROME_IMPERSONATE = "chrome131"

_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

CHROME_XHR_HEADERS: dict[str, str] = {
    "User-Agent": _CHROME_UA,
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


# --- request_id の文脈伝搬 ------------------------------------------

_request_id_var: ContextVar[str | None] = ContextVar("hacomono_request_id", default=None)


def current_request_id() -> str:
    """現在の文脈の request_id を取得。未設定なら自動生成。"""

    rid = _request_id_var.get()
    if rid is None:
        rid = uuid.uuid4().hex[:12]
        _request_id_var.set(rid)
    return rid


def new_request_id() -> str:
    """新しい request_id を発行し、文脈に差し替えて返す。"""

    rid = uuid.uuid4().hex[:12]
    _request_id_var.set(rid)
    return rid


# --- レスポンス型 --------------------------------------------------

@dataclass(slots=True)
class RawResponse:
    """Hacomono API の生レスポンス。"""

    status_code: int
    payload: dict[str, Any]
    elapsed_ms: int
    request_id: str


# --- HTTP バックエンド ---------------------------------------------

class HttpBackend:
    """curl_cffi セッションを 1 つ保持し、Chrome 偽装の HTTP を提供する。

    セッションはプロセス内で使い回し、Cookie（device_id, _at）も永続化される。
    `close()` でセッションを後始末する。
    """

    def __init__(self, *, device_id: str, timeout: float = 15.0) -> None:
        self._timeout = timeout
        self._device_id = device_id
        self._session = ccreq.Session(impersonate=CHROME_IMPERSONATE)
        # Chrome の XHR ヘッダをデフォルトで付与
        self._session.headers.update(CHROME_XHR_HEADERS)
        # device_id Cookie はブラウザの初期訪問で降ってくる 40hex
        self._session.cookies.set(
            "device_id", device_id, domain="reserve.central.co.jp", path="/"
        )

    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def access_token(self) -> str | None:
        return self._session.cookies.get("_at")

    def close(self) -> None:
        try:
            self._session.close()
        except Exception:  # pragma: no cover
            pass

    # --- 呼び出しメソッド ---

    def post_json(
        self,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        referer: str | None = None,
    ) -> RawResponse:
        headers = {"Content-Type": "application/json"}
        if referer:
            headers["Referer"] = referer
        return self._execute("POST", path, headers=headers, json_body=body or {})

    def put_json(
        self,
        path: str,
        body: dict[str, Any],
        *,
        referer: str | None = None,
    ) -> RawResponse:
        headers = {"Content-Type": "application/json"}
        if referer:
            headers["Referer"] = referer
        return self._execute("PUT", path, headers=headers, json_body=body)

    def get_json(self, path: str, *, referer: str | None = None) -> RawResponse:
        headers: dict[str, str] = {}
        if referer:
            headers["Referer"] = referer
        return self._execute("GET", path, headers=headers)

    def get_query_json(
        self,
        path: str,
        query: dict[str, Any],
        *,
        referer: str | None = None,
    ) -> RawResponse:
        """Nuxt の JS と同じ `?query=<URL-encoded JSON>` 形式の GET。"""

        q = urllib.parse.quote(json.dumps(query, ensure_ascii=False))
        target = f"{path}?query={q}"
        headers: dict[str, str] = {}
        if referer:
            headers["Referer"] = referer
        return self._execute("GET", target, headers=headers)

    # --- 内部 ---

    def _execute(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str],
        json_body: dict[str, Any] | None = None,
    ) -> RawResponse:
        url = f"{API_BASE}{path}"
        rid = current_request_id()
        merged_headers: dict[str, str] = {"X-Request-Id": rid, **headers}
        started = time.monotonic()
        try:
            response = self._session.request(
                method=method,
                url=url,
                json=json_body,
                headers=merged_headers,
                timeout=self._timeout,
            )
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            logger.warning(
                "hacomono network_error rid=%s %s %s elapsed=%dms err=%s",
                rid, method, path, elapsed_ms, type(exc).__name__,
            )
            raise UpstreamUnavailable(
                f"{method} {path} failed: {type(exc).__name__}",
            ) from exc
        elapsed_ms = int((time.monotonic() - started) * 1000)

        if response.status_code >= 500:
            raise UpstreamUnavailable(
                f"{method} {path} returned {response.status_code}",
                raw={"status": response.status_code, "text": response.text[:500]},
            )
        if response.status_code == 400:
            raise ValidationError(
                f"{method} {path} returned 400",
                raw={"status": 400, "text": response.text[:500]},
            )

        payload: dict[str, Any]
        try:
            payload = response.json()
            if not isinstance(payload, dict):
                payload = {"data": payload}
        except Exception as exc:
            # 401/403 で body が空のケースがあるため、ここで弾かずに上位層の判定に渡す
            if response.status_code >= 400:
                payload = {}
            else:
                raise ProtocolViolation(
                    f"{method} {path} returned non-JSON body (status={response.status_code})",
                    raw={"text": response.text[:500]},
                ) from exc

        logger.debug(
            "hacomono rid=%s %s %s status=%d elapsed=%dms",
            rid, method, path, response.status_code, elapsed_ms,
        )
        return RawResponse(
            status_code=response.status_code,
            payload=payload,
            elapsed_ms=elapsed_ms,
            request_id=rid,
        )


HttpCall = Callable[[], RawResponse]
