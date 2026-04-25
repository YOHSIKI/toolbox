"""公開月間 API のレスポンスを `Lesson` のリストに変換する。

- `pims_schedule` は「曜日 × 時刻」の週間パターン
- 指定した月の各日付に対し、曜日一致するレッスンを配置する
- `pims_closed` に載っている日は休館扱いで除外
- 予約可否は API 側では判定できないため、`state = UNRESERVABLE`（閲覧専用）で返す
"""

from __future__ import annotations

import calendar as _calendar
import re as _re
import unicodedata as _unicodedata
from datetime import date
from typing import Literal

from app.domain.entities import Lesson, LessonState
from infra.hacomono.public_monthly import PublicMonthlyPayload

_WHITESPACE_RE = _re.compile(r"\s+")

# 公開月間 API のインストラクター名を reserve API と同じ表記に寄せるためのエイリアス。
# reserve API 側が「CS Live」で統一しているのに対し、公開月間は「インストラクター映像」
# という汎用名で返してくる。他に同種の表記揺れが見つかれば追記する。
_INSTRUCTOR_ALIASES: dict[str, str] = {
    "インストラクター映像": "CS Live",
}


def _normalize_display_text(text: str | None) -> str | None:
    """公開月間 API の半角カナ・全角英字等を読みやすい表記に揃える。

    reserve API 側は全角カナ・半角英字・半角スペースが基本だが、公開月間 API
    は半角カナ（`ｼｪｲﾌﾟﾊﾟﾝﾌﾟ`）や全角英字（`ＺｅｎＹｏｇａ`）、前後の空白が
    混ざる。両者を見比べたときに違和感が出るのを避けるため、NFKC で文字種
    を揃え、連続空白を 1 つに圧縮する。
    """

    if text is None:
        return None
    s = str(text)
    if not s:
        return s
    s = _unicodedata.normalize("NFKC", s)
    s = _WHITESPACE_RE.sub(" ", s).strip()
    return s


def _normalize_instructor_name(name: str | None) -> str | None:
    """インストラクター名を reserve API 側の表記に揃える。

    - `"インストラクター映像"` → `"CS Live"`（公式が CS Live 講師を汎用名で返す）
    - `"市川 弘美"` → `"市川"`（reserve API は苗字のみで返すため、フルネームを苗字に圧縮）
    - `"CS Live"` などそのまま reserve と一致するものはそのまま
    """

    base = _normalize_display_text(name)
    if base is None or not base:
        return None
    # 静的エイリアス（全体一致）
    if base in _INSTRUCTOR_ALIASES:
        return _INSTRUCTOR_ALIASES[base]
    # 苗字抽出: 空白区切りの最初の要素（英語名等も「First Last」なら First になるが
    # 現状の hacomono 観測範囲では日本人名が中心なのでこれで十分）
    parts = _WHITESPACE_RE.split(base)
    return parts[0] if parts else base


def _youbi_of_date(d: date) -> str:
    """公開 API の youbi 形式に合わせる（1=月 ... 7=日）。"""

    return str(d.weekday() + 1)


def _hhmm(sttime: str | int | None) -> str | None:
    """'850' → '08:50'、'1115' → '11:15'、'915' → '09:15' に整形する。"""

    if sttime is None:
        return None
    text = str(sttime).strip()
    if not text:
        return None
    text = text.zfill(4)
    try:
        hh = int(text[:-2])
        mm = int(text[-2:])
        if not (0 <= hh < 24 and 0 <= mm < 60):
            return None
        return f"{hh:02d}:{mm:02d}"
    except ValueError:
        return None


def _end_hhmm(start: str | None, duration_min: str | int | None) -> str | None:
    if start is None or duration_min is None:
        return None
    try:
        mins = int(duration_min)
    except (TypeError, ValueError):
        return None
    hh, mm = start.split(":")
    total = int(hh) * 60 + int(mm) + mins
    if total >= 24 * 60:
        total %= 24 * 60
    return f"{total // 60:02d}:{total % 60:02d}"


def _month_iter(year: int, month: int):
    last_day = _calendar.monthrange(year, month)[1]
    for day in range(1, last_day + 1):
        yield date(year, month, day)


def collect_closed_dates(
    payload: PublicMonthlyPayload,
    *,
    year: int,
    month: int,
    kind: Literal["fixed", "special"] | None = None,
) -> set[date]:
    """公開月間 API のレスポンスから、指定月の「通常営業でない日」を返す。

    `pims_closed` の `datekb` 仕様（観測＋公式 PDF 突き合わせ）:
      - "0" = 通常営業日
      - "1" = 定休日（府中店の場合、金曜日）。確実に営業しない
      - "3" = GW/祝日などで**通常プログラムと時間が異なる日**。営業自体は
        行われることがあり、当日 Reserve API で実スケジュールが公開される
        ことがある。月間 API の段階では未配信のため、この関数では「月間
        データには含まれない日」として分離する

    `kind` 引数:
      - `"fixed"` → datekb=1 のみ（店舗定休、仮スケジュール対象外）
      - `"special"` → datekb=3 のみ（祝日特別日、仮スケジュール補完対象）
      - `None` → 従来どおり datekb != "0" を全て返す（後方互換）

    UI 側では `"fixed"` のみを「休館扱い」として参照し、`"special"` は通常の
    欠損日として仮スケジュールで埋める。
    """

    closed_dates: set[date] = set()
    allowed = _closed_datekb_set(kind)
    for entry in payload.closed_days:
        day_raw = entry.get("datebi")
        try:
            day = int(day_raw) if day_raw is not None else None
        except (TypeError, ValueError):
            day = None
        if day is None:
            continue
        datekb = str(entry.get("datekb") or "0")
        if datekb not in allowed:
            continue
        try:
            closed_dates.add(date(year, month, day))
        except ValueError:
            continue
    return closed_dates


def _closed_datekb_set(
    kind: Literal["fixed", "special"] | None,
) -> frozenset[str]:
    if kind == "fixed":
        return frozenset({"1"})
    if kind == "special":
        return frozenset({"3"})
    # kind=None: 従来どおり 0 以外の全て（観測範囲では 1 / 3。未知値も拾う）
    return frozenset({"1", "2", "3", "4", "5", "6", "7", "8", "9"})


def map_public_monthly(
    payload: PublicMonthlyPayload,
    *,
    year: int,
    month: int,
    sisetcd: str,
    studio_id: int,
    studio_room_id: int,
    week_range: tuple[date, date] | None = None,
) -> list[Lesson]:
    """月間データから `Lesson` リストを生成する。

    week_range を指定すると、その範囲の日付だけに絞って返す。
    """

    # 休館日集合（pims_closed は全日メタで、datekb != '0' が休館扱い）
    closed_dates = collect_closed_dates(payload, year=year, month=month)

    # 曜日 → 指定施設のレッスン候補
    by_youbi: dict[str, list[dict]] = {}
    for row in payload.schedule:
        if str(row.get("sisetcd") or "") != sisetcd:
            continue
        youbi = str(row.get("youbi") or "")
        if not youbi:
            continue
        by_youbi.setdefault(youbi, []).append(row)

    lessons: list[Lesson] = []
    for day in _month_iter(year, month):
        if week_range is not None:
            start, end = week_range
            if day < start or day > end:
                continue
        if day in closed_dates:
            continue
        candidates = by_youbi.get(_youbi_of_date(day), [])
        for row in candidates:
            # スクール系（キッズ・合気道等、長期契約/別運用）は除外
            if str(row.get("school") or "0") == "1":
                continue
            program_name_raw = str(row.get("prognm") or "")
            # 予約不要のスクール（yoyakb=1）や、名前が「スクール」で終わるものも除外
            if str(row.get("yoyakb") or "0") == "1":
                continue
            if "スクール" in program_name_raw:
                continue
            start_time = _hhmm(row.get("sttime"))
            if start_time is None:
                continue
            end_time = _end_hhmm(start_time, row.get("totime"))
            program_id = str(row.get("progcd") or "")
            # 半角カナ・全角英字等を NFKC で揃えて表記揺れを吸収
            program_name_display = _normalize_display_text(program_name_raw)
            program_name = program_name_display or program_id or "レッスン"
            instructor_name_raw = row.get("insnm") or row.get("instnm")
            if isinstance(instructor_name_raw, str):
                # reserve API 側と揃える: フルネーム → 苗字のみ、
                # 「インストラクター映像」→「CS Live」等のエイリアス適用
                instructor_name = _normalize_instructor_name(instructor_name_raw)
            else:
                instructor_name = None
            lesson = Lesson(
                studio_lesson_id=0,  # 公開月間には lesson id が無い。予約は不可
                studio_id=studio_id,
                studio_room_id=studio_room_id,
                lesson_date=day,
                start_time=start_time,
                end_time=end_time,
                program_id=program_id,
                program_name=program_name,
                instructor_id=None,
                instructor_name=instructor_name,
                capacity=None,
                remaining_seats=None,
                studio_room_space_id=None,
                space_layout_name=None,
                is_reservable=False,
                reservable_from=None,
                reservable_to=None,
                # 未開放週は「空きあり」相当の見た目でカレンダーに描画し、パネル側で
                # 予約予定フォームに切り替える。is_reservable=False だけは維持
                state=LessonState.AVAILABLE,
                # 公開月間 API 由来の progcd を保存。merge で program_id が reserve
                # の数値 ID に上書きされても source_progcd は残り、alias 学習・
                # lookup のキーになる
                source_progcd=program_id or None,
            )
            lessons.append(lesson)

    lessons.sort(key=lambda lsn: (lsn.lesson_date, lsn.start_time))
    return lessons
