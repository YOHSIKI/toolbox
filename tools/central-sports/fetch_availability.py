"""特定の lesson（studio_room_id + program_id + start_at）の予約状況（残枠等）を取得。

使い方:
  gateway --project toolbox run central-sports.cs-fetch-availability \
    --room 177 --program 19 --start-at '2026-04-23T20:10:00+09:00' \
    --instructor 2149
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cs_api  # noqa: E402
import cs_secrets as local_secrets  # noqa: E402


def _mask(text: str, values: list[str]) -> str:
    out = text
    for v in values:
        if v:
            out = out.replace(v, "***")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--room", type=int, required=True)
    ap.add_argument("--program", type=int, required=True)
    ap.add_argument("--start-at", required=True, help="ISO8601 例: 2026-04-23T20:10:00+09:00")
    ap.add_argument("--instructor", type=int, action="append", default=[])
    ap.add_argument("--identifier", default="central-sports")
    ap.add_argument("--raw", action="store_true")
    args = ap.parse_args()

    cred = local_secrets.get_group(args.identifier)
    mail = cred.get("email") or cred.get("mail_address")
    password = cred.get("password")

    sess = cs_api.Session.new()
    signin = sess.signin(mail_address=mail, password=password)
    if signin.get("errors"):
        print(json.dumps({"phase": "signin", "ok": False, "error_codes": [e.get("code") for e in signin["errors"]]}), file=sys.stderr)
        return 2

    resp = sess.get_reserve_context(
        studio_room_id=args.room,
        program_id=args.program,
        start_at=args.start_at,
        instructor_ids=args.instructor,
    )

    mask_values = [mail, password, sess.device_id]
    at = sess.http.cookies.get("_at")
    if at:
        mask_values.append(at)

    data = resp.get("data") or {}
    errors = resp.get("errors")

    summary = {
        "phase": "reserve-context",
        "ok": not errors,
        "response_top_keys": sorted((resp or {}).keys()),
        "data_type": type(data).__name__,
        "data_top_keys": sorted(data.keys()) if isinstance(data, dict) else None,
        "errors": errors,
    }

    # 興味のあるフィールドを抽出
    if isinstance(data, dict):
        for k in [
            "reserved_count", "max_reservable_count", "is_reservable",
            "reservable_from", "reservable_to", "remain_reservation",
            "capacity", "studio_lesson", "reservation_status",
        ]:
            if k in data:
                summary[k] = data[k]

    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))

    if args.raw:
        raw_json = json.dumps(data, ensure_ascii=False, default=str)
        print("--- RAW data ---", file=sys.stderr)
        print(_mask(raw_json, mask_values), file=sys.stderr)

    sess.signout()
    return 0


if __name__ == "__main__":
    sys.exit(main())
