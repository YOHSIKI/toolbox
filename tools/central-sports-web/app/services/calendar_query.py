"""カレンダー画面で必要な週次スケジュール組み立て。

- gateway で生のレッスン一覧を取得（本番は hacomono、dev モードではフェイク）
- ローカルの予約・定期予約とマージして LessonState を決定
- 右側パネル用に選択中レッスンの SeatMap を取得
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from app.domain.entities import (
    CalendarCell,
    CalendarWeek,
    IntentStatus,
    Lesson,
    LessonState,
    RecurringStatus,
    SeatMap,
    Studio,
)
from app.domain.ports import ReservationGateway
from config.settings import Settings
from db.repositories import (
    intent_repo,
    observed_lesson_repo,
    recurring_repo,
    reservation_repo,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class Selection:
    program_id: str | None = None
    lesson_time: str | None = None
    lesson_date: date | None = None


class CalendarQueryService:
    """カレンダー描画向けのユースケース。"""

    def __init__(
        self,
        db_path: Path,
        gateway: ReservationGateway,
        *,
        hour_range: tuple[int, int] = (9, 21),
        settings: Settings | None = None,
    ) -> None:
        self._db_path = db_path
        self._gateway = gateway
        self._hour_range = hour_range
        self._settings = settings

    def build_week(
        self,
        studio: Studio,
        week_start: date,
        today: date,
        selection: Selection | None = None,
    ) -> CalendarWeek:
        # 予約 API の公開範囲（今日〜+6 日）内は reserve API、範囲外は公開月間 API
        open_until = today + timedelta(days=6)
        out_of_range = week_start > open_until
        lessons: list[Lesson] = []
        # 確定休館（datekb=1）+ 仕様未知の datekb=2 等。datekb=3（祝日特別日）
        # だけは「月間データには無いが仮スケジュールで埋める対象」として除外する。
        closed_days = self._fetch_closed_days(studio, week_start)
        if not out_of_range:
            lessons = self._gateway.fetch_week(studio.ref, week_start, days=7)
            self._annotate_reserved_state(lessons, today=today)
            # release_pending（本日 9:00 開放予定）な枠に対して、定期予約に
            # 一致するなら TARGET（青）表示にする。auto_booking が 9:00 に
            # 拾うため、ユーザーに「自動で予約される枠」だと知らせる。
            self._annotate_recurring_state(lessons, today=today)
            # reserve API は朝 9:00 に「今日+6 日」を新規解放する。9:00 前の
            # 時点では末尾日（今日+6 日）のレッスンがまだ取得できず、カレンダーの
            # 該当列が空になる。このギャップを公開月間 API から補完する。
            covered_dates = {lesson.lesson_date for lesson in lessons}
            week_dates = {week_start + timedelta(days=i) for i in range(7)}
            missing_dates = week_dates - covered_dates - set(closed_days)
            # 過去の日付は補完しない（本当に空だった可能性が高く、後追い情報が誤解を招く）。
            missing_dates = {d for d in missing_dates if d >= today}
            if missing_dates:
                public_lessons = self._fetch_public_monthly_week(studio, week_start)
                fill = [
                    lsn for lsn in public_lessons
                    if lsn.lesson_date in missing_dates
                ]
                if fill:
                    for lesson in fill:
                        # reserve API がまだ開放していない枠。UI では予約予定の
                        # 登録対象として扱う。
                        lesson.release_pending = True
                    self._annotate_recurring_state(fill, today=today)
                    lessons.extend(fill)
                    logger.info(
                        "build_week filled missing dates from public monthly: "
                        "dates=%s fill_lessons=%d",
                        sorted(missing_dates), len(fill),
                    )
        else:
            # 未開放週は公開月間 API から取得する。公開月間 API には座席レイアウト
            # 情報が含まれないため、先に reserve API（今日〜+6 日）を叩いて
            # gateway 側の `_hint_full` / `_hint_name` / `_hint_time` / `_space_index` を温めておく。
            # これにより、同一店舗・同一曜日・同一時刻の lesson に対して同じ
            # studio_room_space_id とレイアウトを復元できる。
            # gateway に 30 秒 TTL キャッシュがあるため、同一リクエスト内の重複
            # 呼び出しは実害なし。
            try:
                self._gateway.fetch_week(studio.ref, today)
            except Exception as exc:  # noqa: BLE001 - warmup 失敗は致命ではない
                logger.warning("calendar warmup fetch_week failed: %s", exc)
            lessons = self._fetch_public_monthly_week(studio, week_start)
            # out_of_range でも recurring が当たる lesson は TARGET 化する。
            # reserve API 経路と違い、reservation / FULL / UNRESERVABLE の判定材料はない。
            self._annotate_recurring_state(lessons, today=today)
        # 祝日特別日（datekb=3）や、翌月未配信で公開月間 API からも取れない日を、
        # 過去 1〜3 週前の observed_lessons から借りて仮スケジュールで埋める。
        # reserve 経路・out_of_range 経路の両方で同じルールが必要なので共通化。
        tentative = self._fill_tentative(
            studio, lessons, week_start, today, set(closed_days)
        )
        if tentative:
            lessons.extend(tentative)
        self._annotate_intent_state(lessons)
        logger.info(
            "build_week studio=%s week=%s out_of_range=%s lessons=%d",
            studio.display_name, week_start, out_of_range, len(lessons),
        )

        settings_hours = (
            self._settings.calendar_start_time if self._settings else self._hour_range[0],
            self._settings.calendar_end_time if self._settings else self._hour_range[1],
        )
        hours = list(range(settings_hours[0], settings_hours[1] + 1))
        days = [week_start + timedelta(days=i) for i in range(7)]
        cell_map = self._to_cell_map(lessons, hours=hours, days=days)
        rows = [
            [
                CalendarCell(hour=h, weekday=w, lessons=cell_map.get((h, w), []))
                for w in range(len(days))
            ]
            for h in hours
        ]

        selected = self._pick_selection(lessons, selection or Selection())
        seat_map = self._resolve_seat_map(selected) if selected is not None else None

        return CalendarWeek(
            studio=studio,
            week_start=week_start,
            week_end=week_start + timedelta(days=len(days) - 1),
            days=days,
            hours=hours,
            rows=rows,
            selected_lesson=selected,
            selected_seat_map=seat_map,
            today=today,
            open_days=7,
            open_until=open_until,
            out_of_range=out_of_range,
            closed_dates=closed_days,
        )

    def _fetch_public_monthly_week(
        self,
        studio: Studio,
        week_start: date,
    ) -> list[Lesson]:
        """予約可能範囲外の週を、公開月間 API（閲覧専用）から取得する。"""

        logger.info(
            "public monthly: studio=%s club_code=%s sisetcd=%s week_start=%s",
            studio.display_name, studio.club_code, studio.sisetcd, week_start,
        )
        if not studio.club_code or not studio.sisetcd:
            logger.warning(
                "public monthly skipped: studio %s has no club_code/sisetcd",
                studio.display_name,
            )
            return []
        week_end = week_start + timedelta(days=6)
        months: set[tuple[int, int]] = set()
        cursor = week_start
        while cursor <= week_end:
            months.add((cursor.year, cursor.month))
            cursor += timedelta(days=1)
        results: list[Lesson] = []
        for year, month in sorted(months):
            try:
                month_lessons = self._gateway.fetch_monthly_public(  # type: ignore[attr-defined]
                    club_code=studio.club_code,
                    sisetcd=studio.sisetcd,
                    studio_id=studio.studio_id,
                    studio_room_id=studio.studio_room_id,
                    year=year,
                    month=month,
                    week_range=(week_start, week_end),
                )
                logger.info(
                    "public monthly fetched year=%d month=%d lessons=%d",
                    year, month, len(month_lessons),
                )
                results.extend(month_lessons)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "public monthly fetch failed year=%d month=%d: %s",
                    year, month, exc,
                )
        return results

    def _fetch_closed_days(self, studio: Studio, week_start: date) -> list[date]:
        """仮スケジュール対象外の休館日を公開月間 API から取得する。

        具体的には `datekb=1`（店舗定休）と仕様不明の `datekb=2` 等を含める。
        `datekb=3`（祝日特別日）だけは「月間データには無いが仮スケジュールで
        埋める対象」として除外する。

        実装上は `kind="all"` から `kind="special"` を差し引いて返す。
        gateway 側にキャッシュがあるので毎リクエストで公開月間 API を叩かない。
        """

        if not studio.club_code or not studio.sisetcd:
            return []
        if not hasattr(self._gateway, "fetch_closed_days"):
            return []
        week_end = week_start + timedelta(days=6)
        months: set[tuple[int, int]] = set()
        cur = week_start
        while cur <= week_end:
            months.add((cur.year, cur.month))
            cur += timedelta(days=1)
        non_tentative: set[date] = set()
        for year, month in sorted(months):
            try:
                all_closed = self._gateway.fetch_closed_days(  # type: ignore[attr-defined]
                    club_code=studio.club_code,
                    sisetcd=studio.sisetcd,
                    year=year,
                    month=month,
                    kind="all",
                )
                special = self._gateway.fetch_closed_days(  # type: ignore[attr-defined]
                    club_code=studio.club_code,
                    sisetcd=studio.sisetcd,
                    year=year,
                    month=month,
                    kind="special",
                )
                non_tentative.update(all_closed - special)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "fetch_closed_days failed year=%d month=%d: %s", year, month, exc
                )
        return sorted(d for d in non_tentative if week_start <= d <= week_end)

    def _fill_tentative(
        self,
        studio: Studio,
        lessons: list[Lesson],
        week_start: date,
        today: date,
        fixed_closed: set[date],
    ) -> list[Lesson]:
        """週内で実データの取れない日を仮スケジュールで補完する。

        `fixed_closed`（datekb=1 の確定休館）と既に埋まっている日を除いた
        残りについて、1/2/3 週前の observed_lessons から lesson を借りる。
        """

        week_dates = {week_start + timedelta(days=i) for i in range(7)}
        covered = {lesson.lesson_date for lesson in lessons}
        targets = week_dates - covered - fixed_closed
        targets = {d for d in targets if d >= today}
        if not targets:
            return []
        tentative = self._build_tentative_lessons(studio, targets, today=today)
        if tentative:
            self._annotate_recurring_state(tentative, today=today)
        return tentative

    def _build_tentative_lessons(
        self,
        studio: Studio,
        target_dates: set[date],
        *,
        today: date,
    ) -> list[Lesson]:
        """target_dates の各日について、過去 1〜3 週前の観測レッスンから
        仮 lesson を組み立てる。

        候補週を新しい順（-7d → -14d → -21d）に評価し、observed_lessons に
        行がある最初の週を採用する。3 週前まで何も無ければその日はスキップ。

        生成 lesson は `studio_lesson_id=0`, `is_reservable=False`,
        `release_pending=True`, `tentative_source=<候補日>` でマークする。
        `release_pending` を立てることで UI の Intent 登録フォームが自動的に
        表示される。

        target × 最大 3 週分の候補日を 1 クエリで引き、日付ごとに辞書化して
        ルックアップする（N+1 を避ける）。
        """

        effective_targets = sorted(d for d in target_dates if d >= today)
        if not effective_targets:
            return []
        candidate_dates: list[date] = []
        for target in effective_targets:
            for weeks_back in (1, 2, 3):
                candidate_dates.append(target - timedelta(weeks=weeks_back))
        observed = observed_lesson_repo.list_by_dates(
            self._db_path,
            studio_id=studio.studio_id,
            studio_room_id=studio.studio_room_id,
            lesson_dates=candidate_dates,
        )

        results: list[Lesson] = []
        for target in effective_targets:
            source_date: date | None = None
            rows: list[dict] = []
            for weeks_back in (1, 2, 3):
                candidate = target - timedelta(weeks=weeks_back)
                candidate_rows = observed.get(candidate)
                if candidate_rows:
                    source_date = candidate
                    rows = candidate_rows
                    break
            if source_date is None or not rows:
                continue
            appended = 0
            for row in rows:
                start_time = str(row.get("start_time") or "")
                program_id = str(row.get("program_id") or "")
                if not start_time:
                    continue
                program_name = str(
                    row.get("program_name") or program_id or "レッスン"
                )
                lesson = Lesson(
                    studio_lesson_id=0,
                    studio_id=studio.studio_id,
                    studio_room_id=studio.studio_room_id,
                    lesson_date=target,
                    start_time=start_time,
                    end_time=None,
                    program_id=program_id,
                    program_name=program_name,
                    instructor_id=row.get("instructor_id"),
                    instructor_name=row.get("instructor_name"),
                    capacity=row.get("capacity"),
                    remaining_seats=None,
                    studio_room_space_id=row.get("studio_room_space_id"),
                    space_layout_name=None,
                    is_reservable=False,
                    reservable_from=None,
                    reservable_to=None,
                    state=LessonState.AVAILABLE,
                    release_pending=True,
                    tentative_source=source_date,
                )
                results.append(lesson)
                appended += 1
            logger.info(
                "tentative filled: target=%s source=%s lessons=%d",
                target, source_date, appended,
            )
        return results

    # --- 内部 -------------------------------------------------------

    def _annotate_reserved_state(
        self, lessons: list[Lesson], *, today: date
    ) -> None:
        """reserve API 経路向けの annotate。

        reservation > recurring (TARGET) > FULL > UNRESERVABLE > AVAILABLE の
        優先順位で state を確定する。intent の付与は別メソッド
        `_annotate_intent_state` で行う。

        recurring の TARGET 化は「今日+6 日より後」に限る。予約 API の開放済み
        範囲（today〜today+6）は定期予約ジョブが既に走り終わった後なので、
        カレンダーに青塗りしても予約は取れず、ユーザーを惑わせるだけ。
        """

        reservations = reservation_repo.list_reservations(self._db_path)
        reservation_index = {
            (r.studio_lesson_id, r.lesson_date, r.lesson_time): r
            for r in reservations
            if r.studio_lesson_id
        }
        recurring_index = self._build_recurring_index()
        recurring_target_from = today + timedelta(days=7)
        for lesson in lessons:
            key = (lesson.studio_lesson_id, lesson.lesson_date, lesson.start_time)
            reservation = reservation_index.get(key)
            if reservation is not None:
                lesson.state = LessonState.RESERVED
                lesson.reserved_seat_no = reservation.seat_no
                lesson.reserved_origin = reservation.origin
                lesson.reserved_reservation_id = reservation.id
                continue
            recur_key = (
                lesson.lesson_date.weekday(),
                lesson.start_time,
                lesson.program_id,
                lesson.studio_id,
                lesson.studio_room_id,
            )
            if (
                lesson.lesson_date >= recurring_target_from
                and recur_key in recurring_index
            ):
                lesson.state = LessonState.TARGET
                continue
            if lesson.remaining_seats == 0:
                lesson.state = LessonState.FULL
            elif not lesson.is_reservable:
                # reserve API は「今日+6 日」の枠を朝 9:00 前から is_reservable=False で
                # 返してくることがある（開放直前の暫定状態）。この枠はまだ予約不可だが、
                # 事前に予約予定（intent）を登録しておけば 9:00 に自動で取れる。
                # よって「予約不可」ではなく release_pending として intent 登録対象に回す。
                release_day = today + timedelta(days=6)
                if lesson.lesson_date == release_day:
                    lesson.release_pending = True
                    lesson.state = LessonState.AVAILABLE
                else:
                    lesson.state = LessonState.UNRESERVABLE
            else:
                lesson.state = LessonState.AVAILABLE

    def _annotate_recurring_state(
        self, lessons: list[Lesson], *, today: date
    ) -> None:
        """公開月間 API 経路向け：recurring に一致する lesson を TARGET 化する。

        reservation / FULL / UNRESERVABLE の判定材料がないため、recurring に
        一致しない lesson は state=AVAILABLE のまま残る。

        公開月間 API の `progcd` と reserve API の `program_id` は値が異なる
        ことがある（別コード体系）。そのため、曜日+時刻+program_id で引いて
        ヒットしなければ、曜日+時刻+program_name で再照合する。

        recurring の TARGET 化は「今日+6 日より後」に限る（実行タイミングが
        既に過ぎている週は対象外）。out_of_range の公開月間 API 経路では
        通常 today+6 以前の週は渡ってこないが、保険として同じフィルタを掛ける。
        """

        recurring_items = [
            r for r in recurring_repo.list_recurring(self._db_path)
            if r.status is RecurringStatus.ACTIVE
        ]
        if not recurring_items:
            return
        recurring_target_from = today + timedelta(days=7)
        id_index = {
            (r.day_of_week, r.start_time, r.program_id, r.studio_id, r.studio_room_id): r
            for r in recurring_items
        }
        name_index = {
            (r.day_of_week, r.start_time, r.program_name, r.studio_id, r.studio_room_id): r
            for r in recurring_items
        }
        # 曜日+時刻+studio だけで引く弱い索引。公開月間 API の progcd/prognm と
        # reserve API の program_id/program_name が週ごとに異なる（CS Live 系
        # ビュープロのようにサブプログラムが週替わり）場合の救済。
        # 同じ店舗・同じ曜日・同じ時刻には通常 1 種類のプログラムしか並ばない
        # という前提に基づくため、false positive は実運用上ほぼ発生しない。
        studio_index = {
            (r.day_of_week, r.start_time, r.studio_id, r.studio_room_id): r
            for r in recurring_items
        }
        for lesson in lessons:
            if lesson.state is LessonState.RESERVED:
                continue
            # release_pending（本日 9:00 に開放される枠）は today+6 なので
            # 通常の境界（today+7）に満たないが、auto_booking が当日 9:00 に
            # 定期予約を拾いにいく対象のため TARGET 化対象に含める。
            if lesson.lesson_date < recurring_target_from and not lesson.release_pending:
                continue
            studio_tuple = (lesson.studio_id, lesson.studio_room_id)
            id_key = (
                lesson.lesson_date.weekday(),
                lesson.start_time,
                lesson.program_id,
                *studio_tuple,
            )
            name_key = (
                lesson.lesson_date.weekday(),
                lesson.start_time,
                lesson.program_name,
                *studio_tuple,
            )
            studio_key = (
                lesson.lesson_date.weekday(),
                lesson.start_time,
                *studio_tuple,
            )
            if id_key in id_index or name_key in name_index or studio_key in studio_index:
                lesson.state = LessonState.TARGET

    def _build_recurring_index(self) -> dict[tuple[int, str, str, str | None, str | None], object]:
        """ACTIVE な定期予約を (曜日, 時刻, プログラム, スタジオ, ルーム) で索引化する。"""

        recurring_items = recurring_repo.list_recurring(self._db_path)
        return {
            (
                r.day_of_week,
                r.start_time,
                r.program_id,
                r.studio_id,
                r.studio_room_id,
            ): r
            for r in recurring_items
            if r.status is RecurringStatus.ACTIVE
        }

    def _annotate_intent_state(self, lessons: list[Lesson]) -> None:
        """予約予定（Intent）登録済みのレッスンに intent_id を付与する。"""

        intents = intent_repo.list_intents(
            self._db_path, status=IntentStatus.PENDING
        )
        intent_index = {
            (
                i.lesson_date,
                i.lesson_time,
                i.program_id,
                i.studio_id,
                i.studio_room_id,
            ): i
            for i in intents
        }
        for lesson in lessons:
            key = (
                lesson.lesson_date,
                lesson.start_time,
                lesson.program_id,
                lesson.studio_id,
                lesson.studio_room_id,
            )
            intent = intent_index.get(key)
            if intent is not None:
                lesson.intent_id = intent.id
                lesson.intent_seat_preferences = list(intent.seat_preferences)

    def _to_cell_map(
        self,
        lessons: list[Lesson],
        *,
        hours: list[int],
        days: list[date],
    ) -> dict[tuple[int, int], list[Lesson]]:
        """(hour, column_index) ごとに複数レッスンを積める辞書を作る。

        column_index は days のインデックス（0 がカレンダー左端）。
        week_start を任意日付にできるよう、weekday() ではなく列位置で引く。
        """

        date_to_col = {d: i for i, d in enumerate(days)}
        hour_set = set(hours)
        cells: dict[tuple[int, int], list[Lesson]] = {}
        for lesson in lessons:
            col = date_to_col.get(lesson.lesson_date)
            if col is None:
                continue
            hour = int(lesson.start_time.split(":")[0])
            if hour not in hour_set:
                continue
            cells.setdefault((hour, col), []).append(lesson)
        for items in cells.values():
            items.sort(key=lambda ls: ls.start_time)
        return cells

    def _pick_selection(
        self,
        lessons: list[Lesson],
        selection: Selection,
    ) -> Lesson | None:
        """クエリで明示的に選択されたレッスンのみ返す。未指定時は None。"""

        if not (selection.lesson_date and selection.program_id and selection.lesson_time):
            return None
        for lesson in lessons:
            if (
                lesson.lesson_date == selection.lesson_date
                and lesson.program_id == selection.program_id
                and lesson.start_time == selection.lesson_time
            ):
                return lesson
        return None

    def _resolve_seat_map(self, lesson: Lesson) -> SeatMap | None:
        try:
            return self._gateway.fetch_seat_map(
                lesson.studio_lesson_id,
                capacity_hint=lesson.capacity,
                studio_room_space_id=lesson.studio_room_space_id,
            )
        except Exception as exc:  # noqa: BLE001 - UI を落とさない
            logger.warning(
                "seat map fetch failed for lesson_id=%d: %s",
                lesson.studio_lesson_id,
                exc,
            )
            return None


def resolve_week_start(raw: str | None, *, today: date) -> date:
    """?week=YYYY-MM-DD をパースする。未指定なら今日を起点にする。

    monday 丸めはせず、指定日から 7 日間を並べる（今日を一番左に）。
    """

    if raw:
        try:
            return date.fromisoformat(raw)
        except ValueError:
            pass
    return today
