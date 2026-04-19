"""Discord Webhook 通知クライアント。

設計原則:
  - 通知失敗は予約処理を止めない（例外を伝播させない）
  - 出力本文はすべて `mask_secrets` を通す
  - レベルごとに Embed の色を変える（緑・黄・朱）
"""

from __future__ import annotations

import logging
from enum import Enum

import httpx

from infra.hacomono.masking import mask_secrets

logger = logging.getLogger(__name__)


class NotifyLevel(str, Enum):
    SUCCESS = "success"
    WARNING = "warning"
    DANGER = "danger"


_LEVEL_COLOR = {
    NotifyLevel.SUCCESS: 0x15803D,
    NotifyLevel.WARNING: 0xB45309,
    NotifyLevel.DANGER: 0xB91C1C,
}


class DiscordNotifier:
    def __init__(self, webhook_url: str | None, *, timeout: float = 5.0) -> None:
        self._webhook_url = webhook_url
        self._timeout = timeout

    def is_enabled(self) -> bool:
        return bool(self._webhook_url)

    def send(
        self,
        *,
        title: str,
        description: str,
        level: NotifyLevel = NotifyLevel.SUCCESS,
        fields: list[tuple[str, str]] | None = None,
    ) -> bool:
        if not self.is_enabled():
            logger.debug("discord disabled, skipping: %s", title)
            return False
        payload = {
            "embeds": [
                {
                    "title": mask_secrets(title),
                    "description": mask_secrets(description),
                    "color": _LEVEL_COLOR.get(level, 0x8A8A84),
                    "fields": [
                        {
                            "name": mask_secrets(name),
                            "value": mask_secrets(value),
                            "inline": False,
                        }
                        for name, value in (fields or [])
                    ],
                }
            ],
        }
        try:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.post(self._webhook_url, json=payload)  # type: ignore[arg-type]
                response.raise_for_status()
            return True
        except Exception as exc:  # noqa: BLE001 - 通知失敗は握りつぶしてログのみ
            logger.warning("discord webhook failed: %s", exc)
            return False
