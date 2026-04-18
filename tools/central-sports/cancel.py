"""指定 reservation_id の予約をキャンセルする。

使い方:
  gateway --project toolbox run central-sports.cs-cancel --reservation 25154326

複数同時キャンセル:
  gateway --project toolbox run central-sports.cs-cancel --reservation 25154326 --reservation 25154327

API: PUT /api/reservation/reservations/cancel  body: {reservation_ids: [id, ...]}
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
    ap.add_argument(
        "--reservation",
        type=int,
        action="append",
        required=True,
        help="キャンセル対象の reservation_id（複数指定可）",
    )
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

    resp = sess.cancel(args.reservation)
    errors = resp.get("errors")
    summary = {
        "phase": "cancel",
        "ok": not errors,
        "reservation_ids": args.reservation,
        "response_top_keys": sorted((resp or {}).keys()),
        "errors": errors,
        "data_type": type(resp.get("data")).__name__,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    sess.signout()
    return 0 if summary["ok"] else 3


if __name__ == "__main__":
    sys.exit(main())
