"""ログインだけを行って成功可否を表示する疎通確認スクリプト。

常駐コンテナまたは `gateway exec` で実行する前提（secrets を復号する必要があるため）。
値そのもの（メール / パスワード / Cookie）は出力に含めない。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.deps import build_context, close_context  # noqa: E402
from config.settings import get_settings  # noqa: E402


def main() -> int:
    settings = get_settings()
    context = build_context(settings)
    try:
        if context.gateway is None or context.auth is None:
            print(json.dumps({"ok": False, "error": "secrets unavailable"}, ensure_ascii=False))
            return 2
        try:
            context.gateway.ensure_authenticated()
        except Exception as exc:  # noqa: BLE001
            print(
                json.dumps(
                    {"ok": False, "error": type(exc).__name__, "message": str(exc)},
                    ensure_ascii=False,
                )
            )
            return 3
        print(
            json.dumps(
                {
                    "ok": True,
                    "is_authenticated": context.auth.is_authenticated,
                    "device_id_length": len(context.device_id),
                    "dry_run": settings.dry_run,
                },
                ensure_ascii=False,
            )
        )
        return 0
    finally:
        close_context(context)


if __name__ == "__main__":
    sys.exit(main())
