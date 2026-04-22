"""設定画面（Phase 2: 編集可）。"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.deps import AppContext, get_context
from app.routes._shared import resolve_current_studio
from app.services.settings_view import build_settings_view
from app.templating import render_page
from db.repositories import app_settings_repo

logger = logging.getLogger(__name__)

router = APIRouter()

BASE_DIR = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "ui" / "templates"))


# --- バリデーション -------------------------------------------------

_HHMM_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


@dataclass
class ValidationResult:
    """バリデーション結果。

    成功時は `db_items` に `(db_key, db_value)` のリストが入る（時刻系は
    hour/minute の 2 レコードに分解される）。失敗時は `error` にメッセージ。
    """

    ok: bool
    db_items: list[tuple[str, str]]
    error: str | None = None


def _validate_time(value: str) -> tuple[int, int] | None:
    m = _HHMM_RE.match(value.strip())
    if m is None:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def _validate_int(value: str, *, min_: int, max_: int) -> int | None:
    try:
        n = int(value.strip())
    except (TypeError, ValueError):
        return None
    if not (min_ <= n <= max_):
        return None
    return n


def _validate_float(value: str, *, min_: float, max_: float) -> float | None:
    try:
        f = float(value.strip())
    except (TypeError, ValueError):
        return None
    if not (min_ <= f <= max_):
        return None
    return f


def validate_setting(
    key: str,
    value: str,
    *,
    current_settings,
) -> ValidationResult:
    """1 項目のバリデーション。整合性は現在値との組み合わせで判定する。

    `current_settings` は現在の Settings インスタンス。
    `alias_sim_warn < alias_sim_accept` / `calendar_start_time < calendar_end_time`
    のような整合性チェックで、編集対象以外の側の値を参照する。
    """

    # --- toggle ----
    if key == "scheduler_enabled":
        v = value.strip().lower()
        if v not in {"true", "false"}:
            return ValidationResult(False, [], "true/false を指定してください")
        return ValidationResult(True, [(key, v)])

    # --- time (HH:MM) → hour/minute 2 レコードに分解 ----
    if key in {
        "login_warmup_time",
        "auto_booking_time",
        "schedule_refresh_time",
        "my_reservations_sync_time",
    }:
        parsed = _validate_time(value)
        if parsed is None:
            return ValidationResult(
                False, [], "時刻は HH:MM（00:00〜23:59）で指定してください"
            )
        hour, minute = parsed
        prefix = key.removesuffix("_time")
        return ValidationResult(
            True,
            [
                (f"{prefix}_hour", str(hour)),
                (f"{prefix}_minute", str(minute)),
            ],
        )

    # --- number (int) ----
    if key == "calendar_start_time":
        n = _validate_int(value, min_=0, max_=23)
        if n is None:
            return ValidationResult(False, [], "0〜23 の整数で指定してください")
        if n >= current_settings.calendar_end_time:
            return ValidationResult(
                False, [], "開始時刻は終了時刻より小さい値である必要があります"
            )
        return ValidationResult(True, [(key, str(n))])

    if key == "calendar_end_time":
        n = _validate_int(value, min_=0, max_=23)
        if n is None:
            return ValidationResult(False, [], "0〜23 の整数で指定してください")
        if n <= current_settings.calendar_start_time:
            return ValidationResult(
                False, [], "終了時刻は開始時刻より大きい値である必要があります"
            )
        return ValidationResult(True, [(key, str(n))])

    if key == "history_display_limit":
        n = _validate_int(value, min_=1, max_=200)
        if n is None:
            return ValidationResult(False, [], "1〜200 の整数で指定してください")
        return ValidationResult(True, [(key, str(n))])

    if key == "max_consecutive_failures":
        n = _validate_int(value, min_=1, max_=10)
        if n is None:
            return ValidationResult(False, [], "1〜10 の整数で指定してください")
        return ValidationResult(True, [(key, str(n))])

    if key == "history_keep_days":
        n = _validate_int(value, min_=1, max_=365)
        if n is None:
            return ValidationResult(False, [], "1〜365 の整数で指定してください")
        return ValidationResult(True, [(key, str(n))])

    # --- number (float) ----
    if key == "alias_sim_accept":
        f = _validate_float(value, min_=0.0, max_=1.0)
        if f is None:
            return ValidationResult(False, [], "0〜1 の範囲で指定してください")
        if f <= current_settings.alias_sim_warn:
            return ValidationResult(
                False, [], "accept は warn より大きい値である必要があります"
            )
        return ValidationResult(True, [(key, f"{f}")])

    if key == "alias_sim_warn":
        f = _validate_float(value, min_=0.0, max_=1.0)
        if f is None:
            return ValidationResult(False, [], "0〜1 の範囲で指定してください")
        if f >= current_settings.alias_sim_accept:
            return ValidationResult(
                False, [], "warn は accept より小さい値である必要があります"
            )
        return ValidationResult(True, [(key, f"{f}")])

    if key in {"reserve_timeout_seconds", "public_monthly_timeout_seconds"}:
        f = _validate_float(value, min_=1.0, max_=60.0)
        if f is None:
            return ValidationResult(False, [], "1〜60 の範囲で指定してください")
        return ValidationResult(True, [(key, f"{f}")])

    return ValidationResult(False, [], f"未知の設定キーです: {key}")


# --- ルート ---------------------------------------------------------


@router.get("/reserve/settings", response_class=HTMLResponse, name="settings")
def settings_view(
    request: Request,
    context: AppContext = Depends(get_context),
) -> HTMLResponse:
    sections = build_settings_view(context.settings)
    studio = resolve_current_studio(request, context.db_path)
    return render_page(
        templates,
        request,
        "settings.html",
        {
            "active_nav": "settings",
            "topbar_title": "設定",
            "topbar_meta": studio.display_name if studio else "",
            "sections": sections,
            "context": context,
            "settings": context.settings,
            # バナー表示用
            "saved": request.query_params.get("saved") == "1",
            "error_message": request.query_params.get("error"),
        },
    )


@router.post("/reserve/settings/update", name="settings_update")
def settings_update(
    request: Request,
    context: AppContext = Depends(get_context),
    key: str = Form(...),
    value: str = Form(...),
) -> RedirectResponse:
    """1 項目を app_settings に upsert する。次回起動で Settings に反映。"""

    result = validate_setting(key, value, current_settings=context.settings)
    if not result.ok:
        message = result.error or "値が不正です"
        logger.info(
            "settings_update rejected key=%s value=%r error=%s",
            key, value, message,
        )
        return RedirectResponse(
            url=f"/reserve/settings?error={quote(message)}",
            status_code=303,
        )
    try:
        for db_key, db_value in result.db_items:
            app_settings_repo.upsert(context.db_path, db_key, db_value)
    except Exception as exc:  # noqa: BLE001
        logger.exception("settings_update db write failed key=%s: %s", key, exc)
        return RedirectResponse(
            url=f"/reserve/settings?error={quote('保存に失敗しました')}",
            status_code=303,
        )
    logger.info(
        "settings_update saved key=%s items=%s",
        key, [k for k, _ in result.db_items],
    )
    return RedirectResponse(url="/reserve/settings?saved=1", status_code=303)
