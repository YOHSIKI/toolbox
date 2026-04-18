"""指定 studio_lesson_id + no で予約 POST する。

使い方:
  gateway --project toolbox run central-sports.cs-reserve --lesson 1228604 --no 1

本物の予約アクションを発行するため注意。実行後、予約一覧で確認可能。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

_t_start = time.monotonic()

import cs_api  # noqa: E402
import cs_secrets as local_secrets  # noqa: E402

_t_import_done = time.monotonic()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lesson", type=int, required=True, help="studio_lesson_id")
    ap.add_argument("--no", type=int, required=True, help="座席番号")
    ap.add_argument("--identifier", default="central-sports")
    args = ap.parse_args()

    cred = local_secrets.get_group(args.identifier)
    mail = cred.get("email") or cred.get("mail_address")
    password = cred.get("password")
    t_secrets_done = time.monotonic()

    sess = cs_api.Session.new()
    signin = sess.signin(mail_address=mail, password=password)
    t_signin_done = time.monotonic()
    if signin.get("errors"):
        print(json.dumps({
            "phase": "signin",
            "ok": False,
            "error_codes": [e.get("code") for e in signin["errors"]],
        }), file=sys.stderr)
        return 2

    resp = sess.reserve(studio_lesson_id=args.lesson, no=args.no)
    t_reserve_done = time.monotonic()
    data = resp.get("data") or {}
    errors = resp.get("errors")
    reservation = data.get("reservation") if isinstance(data, dict) else None

    summary: dict = {
        "phase": "reserve",
        "ok": not errors and reservation is not None,
        "errors": errors,
        "response_top_keys": sorted((resp or {}).keys()),
        "timings_ms": {
            "import": round((_t_import_done - _t_start) * 1000, 1),
            "secrets_decrypt": round((t_secrets_done - _t_import_done) * 1000, 1),
            "signin": round((t_signin_done - t_secrets_done) * 1000, 1),
            "reserve": round((t_reserve_done - t_signin_done) * 1000, 1),
            "total_python": round((t_reserve_done - _t_start) * 1000, 1),
        },
    }
    if isinstance(reservation, dict):
        summary["reservation"] = {
            "id": reservation.get("id"),
            "studio_lesson_id": reservation.get("studio_lesson_id"),
            "no": reservation.get("no"),
            "no_label": reservation.get("no_label"),
            "status": reservation.get("status"),
            "date": reservation.get("date"),
            "start_at": reservation.get("start_at"),
            "end_at": reservation.get("end_at"),
            "studio_id": reservation.get("studio_id"),
            "studio_room_id": reservation.get("studio_room_id"),
            "program_id": reservation.get("program_id"),
            "reservation_type": reservation.get("reservation_type"),
            "position": reservation.get("position"),
            "contract_group_no": reservation.get("contract_group_no"),
            "payment_status": reservation.get("payment_status"),
        }
        program = reservation.get("program") or {}
        studio = reservation.get("studio") or {}
        if isinstance(program, dict):
            summary["reservation"]["program_name"] = program.get("name")
        if isinstance(studio, dict):
            summary["reservation"]["studio_name"] = studio.get("name")

    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    sess.signout()
    return 0 if summary["ok"] else 3


if __name__ == "__main__":
    sys.exit(main())
