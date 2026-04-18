"""自分の予約一覧を取得する。

使い方:
  gateway --project toolbox run central-sports.cs-list-reservations
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cs_api  # noqa: E402
import cs_secrets as local_secrets  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--identifier", default="central-sports")
    ap.add_argument("--raw", action="store_true")
    args = ap.parse_args()

    cred = local_secrets.get_group(args.identifier)
    mail = cred.get("email") or cred.get("mail_address")
    password = cred.get("password")

    sess = cs_api.Session.new()
    signin = sess.signin(mail_address=mail, password=password)
    if signin.get("errors"):
        print(json.dumps({
            "phase": "signin",
            "ok": False,
            "error_codes": [e.get("code") for e in signin["errors"]],
        }), file=sys.stderr)
        return 2

    resp = sess.list_my_reservations()
    data = resp.get("data") or {}
    errors = resp.get("errors")

    reservations = []
    if isinstance(data, dict):
        items = data.get("items") or data.get("reservations") or []
        if isinstance(items, list):
            for r in items:
                if not isinstance(r, dict):
                    continue
                reservations.append({
                    "id": r.get("id"),
                    "studio_lesson_id": r.get("studio_lesson_id"),
                    "date": r.get("date"),
                    "start_at": r.get("start_at"),
                    "end_at": r.get("end_at"),
                    "no": r.get("no"),
                    "studio_id": r.get("studio_id"),
                    "studio_room_id": r.get("studio_room_id"),
                    "program_id": r.get("program_id"),
                    "status": r.get("status"),
                    "reservation_type": r.get("reservation_type"),
                })

    summary = {
        "phase": "list_reservations",
        "ok": not errors,
        "errors": errors,
        "data_top_keys": sorted(data.keys()) if isinstance(data, dict) else None,
        "count": len(reservations),
        "reservations": reservations,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    sess.signout()
    return 0 if summary["ok"] else 3


if __name__ == "__main__":
    sys.exit(main())
