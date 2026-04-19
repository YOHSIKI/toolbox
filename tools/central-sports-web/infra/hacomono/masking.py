"""ログ・通知・画面応答の出力境界で秘匿値を `***` に置換する。

register_secret_values() に email/password/access_token/device_id を登録しておき、
logging / Discord / FastAPI レスポンスなどの出力直前で mask_secrets() を通す。
値が 4 文字未満の場合は誤マスクを避けるために登録しない。
"""

from __future__ import annotations

import logging
import threading

_MASK = "***"
_MIN_LENGTH = 4


class _SecretRegistry:
    """プロセス全体で共有する秘匿値の登録簿。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._values: set[str] = set()

    def register(self, *values: str | None) -> None:
        with self._lock:
            for v in values:
                if v is None:
                    continue
                if len(v) >= _MIN_LENGTH:
                    self._values.add(v)

    def clear(self) -> None:
        with self._lock:
            self._values.clear()

    def snapshot(self) -> list[str]:
        with self._lock:
            # 長い値から置換して、短い値が長い値の一部を誤マスクしないようにする
            return sorted(self._values, key=len, reverse=True)

    def mask(self, text: str) -> str:
        if not text:
            return text
        for v in self.snapshot():
            text = text.replace(v, _MASK)
        return text


_registry = _SecretRegistry()


def register_secret_values(*values: str | None) -> None:
    """秘匿値を登録する。以降の mask_secrets() でマスク対象になる。"""

    _registry.register(*values)


def clear_secret_values() -> None:
    """テストや再起動で登録をリセット。"""

    _registry.clear()


def mask_secrets(text: str) -> str:
    """登録済みの秘匿値を `***` に置換して返す。"""

    return _registry.mask(text)


class MaskingLogFilter(logging.Filter):
    """logging のフィルタとして装着すると、全ログメッセージが自動マスクされる。"""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        masked = mask_secrets(message)
        if masked != message:
            record.msg = masked
            record.args = ()
        return True


def install_log_filter(logger: logging.Logger | None = None) -> None:
    """ルートまたは指定 logger に MaskingLogFilter を挿入する。"""

    target = logger or logging.getLogger()
    mask_filter = MaskingLogFilter()
    target.addFilter(mask_filter)
    for handler in target.handlers:
        handler.addFilter(mask_filter)
