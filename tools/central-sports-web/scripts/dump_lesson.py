"""特定レッスンの生レスポンスをダンプする（デバッグ用）。"""

from __future__ import annotations

import json
import sys

from app.deps import boot_context
from config.settings import get_settings


def main() -> None:
    target_date = sys.argv[1] if len(sys.argv) > 1 else "2026-04-25"
    target_program = int(sys.argv[2]) if len(sys.argv) > 2 else 110

    ctx = boot_context(get_settings())
    assert ctx.gateway is not None, "gateway 未初期化（認証未設定の可能性）"
    client = ctx.gateway._client  # type: ignore[attr-defined]
    client.signin()
    payload = client.fetch_weekly_schedule(79, 177, "2026-04-19", 7)

    items = payload.get("data", {}).get("studio_lessons", {}).get("items", [])
    print(f"items count: {len(items)}", file=sys.stderr)
    for item in items:
        dt = str(item.get("date", ""))[:10]
        pid = item.get("program_id")
        if dt == target_date and pid == target_program:
            print(json.dumps(item, ensure_ascii=False, indent=2))
            return
    print(f"not found: date={target_date} program_id={target_program}", file=sys.stderr)


if __name__ == "__main__":
    main()
