"""Central Sports の指定店舗/ルームのスケジュールを取得して JSON で表示する。

toolbox-exec コンテナでの実行を前提。gateway exec 経由で起動:
  gateway --project toolbox exec tools/central-sports/fetch_schedule.py \
    --studio 79 --room 177 --date 2026-04-25

Secrets から central-sports.mail_address / password を読み、認証後に
/api/master/studio-lessons/schedule をコールする。

出力は値（mail/password/token）を含まない JSON のみ:
  - 店舗名
  - 開示仕様（schedule_open_days, schedule_open_time）
  - 当日の枠リスト（レッスン ID、時刻、プログラム名、残枠、予約可否）
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# このディレクトリを import path に追加（同梱 secrets.py + cs_api.py を読む）
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cs_api  # noqa: E402
import cs_secrets as local_secrets  # noqa: E402


def _mask(text: str, values: list[str]) -> str:
    """文字列から指定値を *** に置換（万一の漏洩防止）。"""
    out = text
    for v in values:
        if v:
            out = out.replace(v, "***")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--studio", type=int, required=True, help="studio_id (例: 79 = 府中)")
    ap.add_argument("--room", type=int, required=True, help="studio_room_id (例: 177)")
    ap.add_argument("--date", required=True, help="date_from YYYY-MM-DD")
    ap.add_argument("--date-to", default=None, help="date_to YYYY-MM-DD（省略可）")
    ap.add_argument(
        "--identifier",
        default="central-sports",
        help="secrets の identifier（mail_address / password キー必須）",
    )
    ap.add_argument(
        "--raw",
        action="store_true",
        help="schedule の raw JSON を吐く（デバッグ用。値のマスク実施済み）",
    )
    args = ap.parse_args()

    cred = local_secrets.get_group(args.identifier)
    mail = cred.get("email") or cred.get("mail_address")
    password = cred.get("password")
    if not mail or not password:
        print(json.dumps({"error": "credentials missing keys 'email' and 'password'"}), file=sys.stderr)
        return 1

    sess = cs_api.Session.new()
    signin_resp = sess.signin(mail_address=mail, password=password)
    if signin_resp.get("errors"):
        err_codes = [e.get("code") for e in signin_resp["errors"]]
        print(json.dumps({
            "phase": "signin",
            "ok": False,
            "error_codes": err_codes,
        }), file=sys.stderr)
        return 2

    resp = sess.get_schedule(
        studio_id=args.studio,
        studio_room_id=args.room,
        date_from=args.date,
        date_to=args.date_to,
    )

    # mail/password/token が万一レスポンスに混入していてもマスクする
    mask_values = [mail, password, sess.device_id]
    at = sess.http.cookies.get("_at")
    if at:
        mask_values.append(at)

    data = resp.get("data") or {}
    studio = data.get("studio") or {}
    schedule = data.get("schedule")

    summary: dict = {
        "phase": "schedule",
        "ok": True,
        "studio": {
            "id": studio.get("id"),
            "name": studio.get("name"),
            "code": studio.get("code"),
            "schedule_open_days": studio.get("schedule_open_days"),
            "schedule_open_time": studio.get("schedule_open_time"),
        },
        "response_top_keys": sorted((resp or {}).keys()),
        "data_top_keys": sorted(data.keys()) if isinstance(data, dict) else None,
        "schedule_type_in_resp": type(schedule).__name__,
        "schedule_present": schedule is not None,
        "errors": (resp or {}).get("errors"),
    }
    if isinstance(schedule, list):
        summary["entry_count"] = len(schedule)
        summary["sample_entry_keys"] = (
            sorted(schedule[0].keys()) if schedule and isinstance(schedule[0], dict) else []
        )
    elif isinstance(schedule, dict):
        summary["schedule_keys"] = sorted(schedule.keys())

    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if args.raw and schedule is not None:
        raw_json = json.dumps(schedule, ensure_ascii=False)
        print("--- RAW SCHEDULE ---", file=sys.stderr)
        print(_mask(raw_json, mask_values), file=sys.stderr)

    sess.signout()
    return 0


if __name__ == "__main__":
    sys.exit(main())
