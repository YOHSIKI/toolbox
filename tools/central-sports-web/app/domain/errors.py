"""ドメイン層の例外。

インフラ例外（`infra/hacomono/errors.py`）とは独立させる。
サービス層で業務的な失敗を表現するときに使う。
"""

from __future__ import annotations


class DomainError(Exception):
    """ドメイン層の例外の基底。"""


class NotFound(DomainError):
    """対象のエンティティが存在しない。"""


class InvariantViolation(DomainError):
    """不変条件違反（希望席が空、未定義店舗など）。"""


class ConcurrencyConflict(DomainError):
    """楽観ロックのコンフリクト等。"""
