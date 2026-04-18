"""既存予約の席番号を変更する（move）。

使い方:
  gateway --project toolbox run central-sports.cs-move --reservation 25154358 --to 2

API: PUT /api/reservation/reservations/move  body: {reservation_id, no}
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
    ap.add_argument("--reservation", type=int, required=True, help="変更対象の reservation_id")
    ap.add_argument("--to", type=int, required=True, help="新しい座席番号 (no)")
    ap.add_argument("--identifier", default="central-sports")
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

    resp = sess.move(reservation_id=args.reservation, no=args.to)
    errors = resp.get("errors")
    summary = {
        "phase": "move",
        "ok": not errors,
        "reservation_id": args.reservation,
        "to_no": args.to,
        "response_top_keys": sorted((resp or {}).keys()),
        "errors": errors,
        "data_type": type(resp.get("data")).__name__,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    sess.signout()
    return 0 if summary["ok"] else 3


if __name__ == "__main__":
    sys.exit(main())
