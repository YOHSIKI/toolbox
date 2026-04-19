"""デバッグ用のエンドポイント（本番で調査中に使う）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from app.deps import AppContext, get_context
from app.routes._shared import resolve_current_studio
from fastapi import Request

router = APIRouter()


@router.get("/debug/raw-lesson", name="debug_raw_lesson")
def raw_lesson(
    request: Request,
    date: str = Query(..., description="YYYY-MM-DD"),
    program_id: int = Query(..., description="hacomono の program_id"),
    week_start: str = Query(..., description="スケジュール取得開始日 YYYY-MM-DD"),
    context: AppContext = Depends(get_context),
) -> JSONResponse:
    """reserve API の生レスポンスから該当レッスンを JSON で返す。"""

    if context.client is None:
        return JSONResponse({"error": "client uninitialized"}, status_code=503)
    studio = resolve_current_studio(request, context.db_path)
    if studio is None:
        return JSONResponse({"error": "no studio"}, status_code=400)
    from datetime import date as _date, timedelta as _td
    start = _date.fromisoformat(week_start)
    end = start + _td(days=6)
    response = context.client.fetch_schedule(
        studio_id=studio.studio_id,
        studio_room_id=studio.studio_room_id,
        date_from=start.isoformat(),
        date_to=end.isoformat(),
    )
    payload = response.payload
    items = payload.get("data", {}).get("studio_lessons", {}).get("items", [])
    for item in items:
        if str(item.get("date", ""))[:10] == date and int(item.get("program_id", 0)) == program_id:
            return JSONResponse(item)
    return JSONResponse({"error": "not found", "count": len(items)}, status_code=404)


@router.get("/debug/program-space-matrix", name="debug_program_space_matrix")
def program_space_matrix(
    request: Request,
    week_start: str = Query(..., description="YYYY-MM-DD"),
    context: AppContext = Depends(get_context),
) -> JSONResponse:
    """今週の reserve API レッスンを集計し、program_id → 使用 space_id の
    セットと曜日×時刻の内訳を返す。program_id だけで座席配置が一意に決まるかを検証する。"""

    if context.client is None:
        return JSONResponse({"error": "client uninitialized"}, status_code=503)
    studio = resolve_current_studio(request, context.db_path)
    if studio is None:
        return JSONResponse({"error": "no studio"}, status_code=400)
    from datetime import date as _date, timedelta as _td
    start = _date.fromisoformat(week_start)
    end = start + _td(days=6)
    response = context.client.fetch_schedule(
        studio_id=studio.studio_id,
        studio_room_id=studio.studio_room_id,
        date_from=start.isoformat(),
        date_to=end.isoformat(),
    )
    sl = response.payload.get("data", {}).get("studio_lessons", {})
    items = sl.get("items", [])
    programs = {p.get("id"): p for p in sl.get("programs", []) if isinstance(p, dict)}
    # program_id -> { space_id -> list of (date, time) }
    matrix: dict[int, dict[int, list[str]]] = {}
    for it in items:
        pid = it.get("program_id")
        sid = it.get("studio_room_space_id")
        date_str = str(it.get("date", ""))[:10]
        start_at = str(it.get("start_at", ""))
        # HH:MM を抽出
        hhmm = ""
        if "T" in start_at:
            t = start_at.split("T")[1][:5]
            hhmm = t
        entry = f"{date_str} {hhmm}"
        by_space = matrix.setdefault(pid, {})
        by_space.setdefault(sid, []).append(entry)
    # 整形
    out = []
    for pid, by_space in sorted(matrix.items(), key=lambda kv: kv[0] if kv[0] is not None else -1):
        name = (programs.get(pid) or {}).get("name") or str(pid)
        space_ids = list(by_space.keys())
        # スクール系は除外（登録できない）
        if "スクール" in name:
            continue
        entry = {
            "program_id": pid,
            "program_name": name,
            "space_id_count": len(space_ids),
            "lesson_count": sum(len(v) for v in by_space.values()),
            "by_space": {str(sid): occurs for sid, occurs in by_space.items()},
        }
        out.append(entry)
    # space_id_count > 1 のもの（つまりプログラム名だけでは座席配置が決まらないもの）を先頭に
    out.sort(key=lambda e: (-e["space_id_count"], -e["lesson_count"]))
    return JSONResponse({"total_programs": len(out), "programs": out})


@router.get("/debug/my-reservations", name="debug_my_reservations")
def my_reservations(
    context: AppContext = Depends(get_context),
) -> JSONResponse:
    """/api/reservation/reservations の生レスポンスを返す（確認用）。"""

    if context.client is None:
        return JSONResponse({"error": "client uninitialized"}, status_code=503)
    response = context.client.list_my_reservations()
    return JSONResponse(response.payload)


@router.get("/debug/spaces", name="debug_spaces")
def debug_spaces(
    request: Request,
    week_start: str = Query(..., description="YYYY-MM-DD"),
    context: AppContext = Depends(get_context),
) -> JSONResponse:
    """schedule API が返す studio_room_spaces（座席レイアウト）全件を返す。"""

    if context.client is None:
        return JSONResponse({"error": "client uninitialized"}, status_code=503)
    studio = resolve_current_studio(request, context.db_path)
    if studio is None:
        return JSONResponse({"error": "no studio"}, status_code=400)
    from datetime import date as _date, timedelta as _td
    start = _date.fromisoformat(week_start)
    end = start + _td(days=6)
    response = context.client.fetch_schedule(
        studio_id=studio.studio_id,
        studio_room_id=studio.studio_room_id,
        date_from=start.isoformat(),
        date_to=end.isoformat(),
    )
    sl = response.payload.get("data", {}).get("studio_lessons", {})
    return JSONResponse({"studio_room_spaces": sl.get("studio_room_spaces", [])})


@router.get("/debug/at-time", name="debug_at_time")
def at_time(
    request: Request,
    date: str = Query(..., description="YYYY-MM-DD"),
    time: str = Query(..., description="HH:MM"),
    week_start: str = Query(..., description="YYYY-MM-DD（予約 API の窓）"),
    context: AppContext = Depends(get_context),
) -> JSONResponse:
    """指定日時に該当する全レッスンの (id, program_id, program_name, instructor) を返す。"""

    if context.client is None:
        return JSONResponse({"error": "client uninitialized"}, status_code=503)
    studio = resolve_current_studio(request, context.db_path)
    if studio is None:
        return JSONResponse({"error": "no studio"}, status_code=400)
    from datetime import date as _date, timedelta as _td
    start = _date.fromisoformat(week_start)
    end = start + _td(days=6)
    response = context.client.fetch_schedule(
        studio_id=studio.studio_id,
        studio_room_id=studio.studio_room_id,
        date_from=start.isoformat(),
        date_to=end.isoformat(),
    )
    sl = response.payload.get("data", {}).get("studio_lessons", {})
    items = sl.get("items", [])
    programs = {p.get("id"): p for p in sl.get("programs", []) if isinstance(p, dict)}
    instructors = {i.get("id"): i for i in sl.get("instructors", []) if isinstance(i, dict)}
    hh, mm = time.split(":")
    target_prefix = f"T{hh.zfill(2)}:{mm.zfill(2)}"
    hits = []
    for it in items:
        if str(it.get("date", ""))[:10] != date:
            continue
        if target_prefix not in str(it.get("start_at", "")):
            continue
        pid = it.get("program_id")
        iid = it.get("instructor_id")
        hits.append({
            "lesson_id": it.get("id"),
            "program_id": pid,
            "program_name": (programs.get(pid) or {}).get("name"),
            "instructor_id": iid,
            "instructor_name": (instructors.get(iid) or {}).get("nick_name") or (instructors.get(iid) or {}).get("name"),
            "start_at": it.get("start_at"),
        })
    return JSONResponse({"count": len(hits), "hits": hits})
