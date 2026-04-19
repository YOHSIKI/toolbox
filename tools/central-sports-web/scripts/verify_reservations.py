"""自分の予約一覧を取得して表示する。"""

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
        if context.gateway is None:
            print(json.dumps({"ok": False, "error": "secrets unavailable"}, ensure_ascii=False))
            return 2
        context.gateway.ensure_authenticated()
        reservations = context.gateway.fetch_my_reservations()
        summary = {
            "ok": True,
            "count": len(reservations),
            "reservations": [
                {
                    "external_id": r.external_id,
                    "studio_lesson_id": r.studio_lesson_id,
                    "date": r.lesson_date.isoformat(),
                    "time": r.lesson_time,
                    "program": r.program_name,
                    "seat_no": r.seat_no,
                    "status": r.status.value,
                }
                for r in reservations
            ],
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(
            json.dumps(
                {"ok": False, "error": type(exc).__name__, "message": str(exc)},
                ensure_ascii=False,
            )
        )
        return 3
    finally:
        close_context(context)


if __name__ == "__main__":
    sys.exit(main())
