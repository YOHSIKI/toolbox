"""Central Sports の指定店舗/ルームのスケジュールを取得して JSON で表示する。

toolbox-exec コンテナでの実行を前提。gateway custom runner 経由で起動:
  gateway --project toolbox run central-sports.cs-fetch-schedule \
    --studio 79 --room 177 --date 2026-04-25

Secrets から central-sports.email / password を読み、認証後に
/api/master/studio-lessons/schedule をコールする。

出力は値（mail/password/token）を一切含まない JSON:
  - 店舗情報（名称、schedule_open_days/time）
  - レッスン一覧（ID、開始/終了時刻、program_id、instructor_id、残枠）
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


def _summarize_lesson(item: dict) -> dict:
    """レッスン 1 件を要約（機微でない識別子のみ）。"""
    return {
        "id": item.get("id"),
        "date": item.get("date"),
        "start_at": item.get("start_at"),
        "end_at": item.get("end_at"),
        "program_id": item.get("program_id"),
        "instructor_id": item.get("instructor_id"),
        "studio_room_space_id": item.get("studio_room_space_id"),
        "reservation_status": item.get("reservation_status"),
        "remain_reservation": item.get("remain_reservation"),
        "reservable_from": item.get("reservable_from"),
        "reservable_to": item.get("reservable_to"),
        "is_reservable": item.get("is_reservable"),
        "capacity": item.get("capacity"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--studio", type=int, required=True, help="studio_id")
    ap.add_argument("--room", type=int, required=True, help="studio_room_id")
    ap.add_argument("--date", required=True, help="date_from YYYY-MM-DD")
    ap.add_argument("--date-to", default=None)
    ap.add_argument("--identifier", default="central-sports")
    ap.add_argument("--raw", action="store_true", help="全レッスンの raw JSON を stderr に出力")
    ap.add_argument("--with-spaces", action="store_true", help="is_reservable な lesson に listNos を叩いて空き space 数を付与")
    args = ap.parse_args()

    cred = local_secrets.get_group(args.identifier)
    mail = cred.get("email") or cred.get("mail_address")
    password = cred.get("password")
    if not mail or not password:
        print(json.dumps({"error": "credentials missing: email/mail_address + password"}), file=sys.stderr)
        return 1

    sess = cs_api.Session.new()
    signin = sess.signin(mail_address=mail, password=password)
    if signin.get("errors"):
        print(json.dumps({
            "phase": "signin",
            "ok": False,
            "error_codes": [e.get("code") for e in signin["errors"]],
        }), file=sys.stderr)
        return 2

    resp = sess.get_schedule(
        studio_id=args.studio,
        studio_room_id=args.room,
        date_from=args.date,
        date_to=args.date_to,
    )

    mask_values = [mail, password, sess.device_id]
    at = sess.http.cookies.get("_at")
    if at:
        mask_values.append(at)

    data = resp.get("data") or {}
    studio = data.get("studio") or {}
    studio_lessons = data.get("studio_lessons") or {}
    items = []
    programs_map = {}
    instructors_map = {}
    # studio_room_space_id -> space_num (capacity)
    capacity_by_space_id: dict[int, int] = {}
    space_name_by_id: dict[int, str] = {}
    if isinstance(studio_lessons, dict):
        items = studio_lessons.get("items") or []
        programs_map = {
            p.get("id"): p.get("name")
            for p in (studio_lessons.get("programs") or [])
        }
        instructors_map = {
            i.get("id"): i.get("nick_name") or i.get("name")
            for i in (studio_lessons.get("instructors") or [])
        }
        for space in (studio_lessons.get("studio_room_spaces") or []):
            sid = space.get("id")
            if sid is None:
                continue
            capacity_by_space_id[sid] = space.get("space_num")
            space_name_by_id[sid] = space.get("name")

    lessons = [_summarize_lesson(it) for it in items if isinstance(it, dict)]
    # 名前解決
    for l in lessons:
        l["program_name"] = programs_map.get(l.get("program_id"))
        l["instructor_name"] = instructors_map.get(l.get("instructor_id"))

    # --with-spaces: 各 is_reservable lesson で:
    #   capacity = lesson の studio_room_space_id から該当レイアウトの space_num を参照
    #   reserved = listNos の件数（listNos は「予約済み no」を返す）
    #   available = capacity - reserved
    if args.with_spaces:
        for l in lessons:
            sid = l.get("studio_room_space_id")
            l["capacity"] = capacity_by_space_id.get(sid)
            l["space_layout_name"] = space_name_by_id.get(sid)
            if not l.get("is_reservable"):
                l["reserved_count"] = None
                l["available_count"] = None
                continue
            try:
                nos_resp = sess.list_nos(l["id"])
                reserved_nos = (nos_resp.get("data") or {}).get("nos") or []
                l["reserved_count"] = len(reserved_nos)
                cap = l["capacity"]
                l["available_count"] = (cap - len(reserved_nos)) if cap is not None else None
            except Exception:
                l["reserved_count"] = None
                l["available_count"] = None

    summary = {
        "phase": "schedule",
        "ok": True,
        "studio": {
            "id": studio.get("id"),
            "name": studio.get("name"),
            "schedule_open_days": studio.get("schedule_open_days"),
            "schedule_open_time": studio.get("schedule_open_time"),
        },
        "room": {"id": args.room},
        "date_range": {"from": args.date, "to": args.date_to},
        "lesson_count": len(lessons),
        "lessons": lessons,
    }

    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if args.raw:
        raw_json = json.dumps(studio_lessons, ensure_ascii=False, default=str)
        print("--- RAW studio_lessons ---", file=sys.stderr)
        print(_mask(raw_json, mask_values), file=sys.stderr)

    sess.signout()
    return 0


if __name__ == "__main__":
    sys.exit(main())
