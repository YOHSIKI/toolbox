"""Hacomono 予約 API に関するエラー階層。

1 次の分類は「どう対応すべきか」で分ける。認証失効なら再ログイン、業務エラーなら
代替席や停止、5xx なら短期バックオフ、という上位層の判断を楽にする。
"""

from __future__ import annotations

from typing import Any


class HacomonoError(Exception):
    """Hacomono 由来のエラーの基底。"""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        raw: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.raw = raw

    def __repr__(self) -> str:  # pragma: no cover - デバッグ用
        return f"{type(self).__name__}({self.args[0]!r}, code={self.code!r})"


# ------------------------------------------------------------
# 認証系
# ------------------------------------------------------------
class AuthenticationError(HacomonoError):
    """認証系の失敗全般。"""


class InvalidCredentials(AuthenticationError):
    """メールアドレスまたはパスワードが誤っている。再試行しても通らない。"""


class SessionExpired(AuthenticationError):
    """アクセストークンが失効した。1 回だけ再ログインを試みる。"""


class LockedOut(AuthenticationError):
    """連続再ログイン失敗で停止した状態。運用者の手動介入が必要。"""


# ------------------------------------------------------------
# ビジネスルール違反（業務エラー）
# ------------------------------------------------------------
class BusinessRuleViolation(HacomonoError):
    """予約 API のビジネスルールによる拒否。"""


class AlreadyReserved(BusinessRuleViolation):
    """自分が既にそのレッスンを予約済み。"""


class SeatUnavailable(BusinessRuleViolation):
    """希望席が取れなかった。次の候補を試す契機。"""


class OutsideReservationWindow(BusinessRuleViolation):
    """予約可能期間外（9:00 前や 7 日を超える先）。"""


class CapacityExceeded(BusinessRuleViolation):
    """レッスン全体が満席。"""


# ------------------------------------------------------------
# 技術的な失敗
# ------------------------------------------------------------
class UpstreamUnavailable(HacomonoError):
    """5xx / タイムアウト / DNS などの一時的な外部障害。短期バックオフで retry 可。"""


class ValidationError(HacomonoError):
    """400 系の入力不正。実装バグの可能性が高く、retry しない。"""


class ProtocolViolation(HacomonoError):
    """想定外のレスポンス形式。hacomono 側の仕様変更の疑い。"""
