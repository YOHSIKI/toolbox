"""既定店舗の当週スケジュールを取得して件数を表示する。"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.deps import build_context, close_context  # noqa: E402
from app.domain.entities import StudioRef  # noqa: E402
from config.settings import get_settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--studio-id", type=int, default=None, help="省略時は既定店舗")
    parser.add_argument("--studio-room-id", type=int, default=None)
    parser.add_argument("--days-ahead", type=int, default=0, help="何日後の週を取るか")
    args = parser.parse_args()

    settings = get_settings()
    context = build_context(settings)
    try:
        if context.gateway is None:
            print(json.dumps({"ok": False, "error": "secrets unavailable"}, ensure_ascii=False))
            return 2
        context.gateway.ensure_authenticated()
        today = datetime.now(tz=settings.tz).date()
        target_day = today + timedelta(days=args.days_ahead)
        week_start = target_day - timedelta(days=target_day.weekday())
        studio_id = args.studio_id or settings.default_studio_id
        studio_room_id = args.studio_room_id or settings.default_studio_room_id
        lessons = context.gateway.fetch_week(
            StudioRef(studio_id, studio_room_id),
            week_start,
        )
        summary = {
            "ok": True,
            "studio_id": studio_id,
            "studio_room_id": studio_room_id,
            "week_start": week_start.isoformat(),
            "lesson_count": len(lessons),
            "sample": [
                {
                    "studio_lesson_id": lsn.studio_lesson_id,
                    "date": lsn.lesson_date.isoformat(),
                    "start_time": lsn.start_time,
                    "program": lsn.program_name,
                    "instructor": lsn.instructor_name,
                    "remaining": lsn.remaining_seats,
                    "capacity": lsn.capacity,
                    "state": lsn.state.value,
                }
                for lsn in lessons[:10]
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
