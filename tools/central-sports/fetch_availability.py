"""特定の studio_lesson（ID で指定）の予約コンテキストを取得する。

使い方:
  gateway --project toolbox run central-sports.cs-fetch-availability --lesson 1228610
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
    ap.add_argument("--lesson", type=int, required=True, help="studio_lesson_id")
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

    # まず listNos で no 候補を取得（コンポーネントの初期化順に合わせる）
    nos_resp = sess.list_nos(args.lesson)
    nos_data = nos_resp.get("data") or {}
    nos = nos_data.get("nos") or []

    resp = sess.get_reserve_context(studio_lesson_id=args.lesson)

    mask_values = [mail, password, sess.device_id]
    at = sess.http.cookies.get("_at")
    if at:
        mask_values.append(at)

    data = resp.get("data") or {}
    errors = resp.get("errors")

    summary: dict = {
        "phase": "context",
        "ok": not errors,
        "data_top_keys": sorted(data.keys()) if isinstance(data, dict) else None,
        "errors": errors,
        "nos_sample": nos[:5] if isinstance(nos, list) else None,
        "nos_count": len(nos) if isinstance(nos, list) else 0,
        "nos_response_errors": nos_resp.get("errors"),
    }

    if isinstance(data, dict):
        sl = data.get("studio_lesson") or {}
        summary["studio_lesson"] = {
            "id": sl.get("id"),
            "start_at": sl.get("start_at"),
            "end_at": sl.get("end_at"),
            "reserved_count": sl.get("reserved_count"),
            "max_reservable_count": sl.get("max_reservable_count"),
            "is_reservable": sl.get("is_reservable"),
            "reservable_from": sl.get("reservable_from"),
            "reservable_to": sl.get("reservable_to"),
            "reservation_status": sl.get("reservation_status"),
            "status": sl.get("status"),
        } if sl else None
        summary["reserve_processions_count"] = len(data.get("reserve_processions") or [])
        summary["reservation_priorities_count"] = len(data.get("reservation_priorities") or [])
        summary["my_reservation"] = data.get("my_reservation")
        summary["tickets_count"] = len(data.get("tickets") or [])

    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))

    if args.raw:
        raw_json = json.dumps(data, ensure_ascii=False, default=str)
        print("--- RAW data ---", file=sys.stderr)
        print(_mask(raw_json, mask_values), file=sys.stderr)

    sess.signout()
    return 0


if __name__ == "__main__":
    sys.exit(main())
