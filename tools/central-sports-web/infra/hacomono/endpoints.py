"""Hacomono 予約 API のエンドポイント定数。

パスと Referer の組み立てに集約。URL の完全形は HttpBackend が組む。
"""

from __future__ import annotations

BASE_URL = "https://reserve.central.co.jp"
API_BASE = f"{BASE_URL}/api"

# 認証
SIGNIN_PATH = "/system/auth/signin"
SIGNOUT_PATH = "/system/auth/signout"
AUTH_DETAIL_PATH = "/system/auth/detail"

# スケジュール
SCHEDULE_PATH = "/master/studio-lessons/schedule"

# 予約
RESERVATIONS_PATH = "/reservation/reservations"
RESERVE_PATH = "/reservation/reservations/reserve"
CANCEL_PATH = "/reservation/reservations/cancel"
MOVE_PATH = "/reservation/reservations/move"


def list_nos_path(studio_lesson_id: int) -> str:
    """指定レッスンの予約済み席番号一覧のパス。"""

    return f"/reservation/reservations/{studio_lesson_id}/no"


def login_referer() -> str:
    """ログイン API 呼び出し時の Referer。"""

    return f"{BASE_URL}/app/login"


def schedule_referer(studio_id: int, studio_room_id: int) -> str:
    """スケジュール API 呼び出し時の Referer。"""

    return f"{BASE_URL}/reserve/schedule/{studio_id}/{studio_room_id}/"


def reservations_referer() -> str:
    """予約一覧・変更系の Referer。"""

    return f"{BASE_URL}/reserve/reservation/"
