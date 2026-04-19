"""定期予約の CRUD と実行。

CRUD は DB への書き込みのみ。実行（`execute_one`）は 9:00 ジョブと手動トリガから呼ぶ。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

from app.domain.entities import (
    HistoryCategory,
    HistoryEntry,
    HistoryResult,
    Lesson,
    OccurrencePreview,
    RecurringReservation,
    RecurringStatus,
    Reservation,
    ReservationOrigin,
    ReservationStatus,
    SeatMap,
    Studio,
    StudioRef,
    next_weekday,
)
from app.domain.errors import NotFound
from app.domain.ports import ReservationGateway
from config.settings import Settings
from db.repositories import history_repo, recurring_repo, reservation_repo, studio_repo

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RecurringRunResult:
    recurring_id: str
    target_date: date
    ok: bool
    seat_no: int | None
    external_id: int | None
    message: str
    failure_reason: str | None = None


class RecurringService:
    def __init__(
        self,
        db_path: Path,
        gateway: ReservationGateway,
        settings: Settings,
    ) -> None:
        self._db_path = db_path
        self._gateway = gateway
        self._settings = settings

    # --- CRUD -----------------------------------------------------

    def create(
        self,
        *,
        day_of_week: int,
        start_time: str,
        program_id: str,
        program_name: str,
        studio_id: int,
        studio_room_id: int,
        seat_preferences: list[int],
        note: str | None = None,
    ) -> RecurringReservation:
        item = RecurringReservation(
            id=uuid.uuid4().hex,
            day_of_week=day_of_week,
            start_time=start_time,
            program_id=program_id,
            program_name=program_name,
            studio_id=studio_id,
            studio_room_id=studio_room_id,
            seat_preferences=seat_preferences,
            status=RecurringStatus.ACTIVE,
            note=note,
        )
        recurring_repo.insert_recurring(self._db_path, item)
        return item

    def update(self, item: RecurringReservation) -> None:
        recurring_repo.update_recurring(self._db_path, item)

    def update_seats(
        self, recurring_id: str, seat_preferences: list[int]
    ) -> RecurringReservation:
        """登録済みの定期予約の希望席だけを更新する（取消→再登録を不要にする）。"""

        item = recurring_repo.get_recurring(self._db_path, recurring_id)
        if item is None:
            raise NotFound(f"recurring {recurring_id} not found")
        if item.status is not RecurringStatus.ACTIVE:
            raise NotFound(
                f"recurring {recurring_id} is not editable (status={item.status.value})"
            )
        recurring_repo.update_seat_preferences(
            self._db_path, recurring_id, seat_preferences
        )
        item.seat_preferences = list(seat_preferences)
        return item

    def set_status(self, recurring_id: str, status: RecurringStatus) -> None:
        recurring_repo.update_status(self._db_path, recurring_id, status)

    def get(self, recurring_id: str) -> RecurringReservation | None:
        return recurring_repo.get_recurring(self._db_path, recurring_id)

    def list_active(self) -> list[RecurringReservation]:
        return [
            r
            for r in recurring_repo.list_recurring(self._db_path)
            if r.status is RecurringStatus.ACTIVE
        ]

    def pick_default(
        self,
        preferred_id: str | None,
    ) -> RecurringReservation | None:
        items = recurring_repo.list_recurring(self._db_path)
        if preferred_id:
            for item in items:
                if item.id == preferred_id:
                    return item
        return items[0] if items else None

    # --- 座席レイアウト解決 ----------------------------------------

    def _resolve_seat_map_by_slot(
        self,
        *,
        studio_id: int,
        studio_room_id: int,
        program_name: str,
        day_of_week: int,
        start_time: str,
        log_tag: str,
    ) -> SeatMap | None:
        """gateway の学習 hint から space_id を逆引きして SeatMap を組み立てる。

        見つからなければ None（UI 側は 1〜35 連番にフォールバック）。
        `resolve_seat_map_for_item` / `resolve_seat_map_for_slot` の共通実装。
        """

        gateway = self._gateway
        lookup_hint = getattr(gateway, "_lookup_hint", None)
        if lookup_hint is None:
            return None
        try:
            space_id = lookup_hint(
                studio_id=studio_id,
                studio_room_id=studio_room_id,
                program_name=program_name,
                day_of_week=day_of_week,
                start_time=start_time,
            )
        except Exception as exc:  # noqa: BLE001 - hint lookup は UI を止めない
            logger.warning(
                "resolve_seat_map: _lookup_hint failed (%s): %s",
                log_tag, exc,
            )
            return None
        if space_id is None:
            return None
        space_index = getattr(gateway, "_space_index", {}) or {}
        positions = list(space_index.get(space_id, ()))
        if not positions:
            return None
        space_grid = getattr(gateway, "_space_grid", {}) or {}
        grid_cols, grid_rows = space_grid.get(space_id, (0, 0))
        if not grid_cols or not grid_rows:
            grid_cols = max(p.coord_x for p in positions) + 1
            grid_rows = max(p.coord_y for p in positions) + 1
        return SeatMap(
            studio_lesson_id=0,  # recurring は lesson_id 固定でない
            capacity=len(positions),
            taken_nos=[],
            positions=positions,
            grid_cols=grid_cols,
            grid_rows=grid_rows,
        )

    def resolve_seat_map_for_item(
        self, item: RecurringReservation
    ) -> SeatMap | None:
        """recurring に対応する座席レイアウトを返す。

        gateway に学習済みの (program_name, weekday, start_time) → space_id 索引で
        space_id を引き、_space_index から positions を取得する。
        見つからなければ None（編集画面では 1〜35 連番フォールバック）。
        """

        return self._resolve_seat_map_by_slot(
            studio_id=item.studio_id,
            studio_room_id=item.studio_room_id,
            program_name=item.program_name,
            day_of_week=item.day_of_week,
            start_time=item.start_time,
            log_tag=f"pid={item.id}",
        )

    def resolve_seat_map_for_slot(
        self,
        *,
        studio_id: int,
        studio_room_id: int,
        program_name: str,
        day_of_week: int,
        start_time: str,
    ) -> SeatMap | None:
        """曜日・時刻・プログラム名から座席レイアウトを逆引きする。

        「追加」画面で `(wd, time, program)` の 3 段選択の各組み合わせについて
        事前にレイアウトを解決しておくために使う。
        """

        return self._resolve_seat_map_by_slot(
            studio_id=studio_id,
            studio_room_id=studio_room_id,
            program_name=program_name,
            day_of_week=day_of_week,
            start_time=start_time,
            log_tag=(
                f"studio={studio_id}/{studio_room_id} wd={day_of_week} "
                f"time={start_time} name={program_name!r}"
            ),
        )

    # --- 配置プレビュー ---------------------------------------------

    def build_occurrences(
        self,
        item: RecurringReservation,
        *,
        today: date,
        weeks: int = 4,
    ) -> list[OccurrencePreview]:
        run_hour = self._settings.run_hour
        run_minute = self._settings.run_minute
        base = next_weekday(today, item.day_of_week)
        studio_ref = StudioRef(item.studio_id, item.studio_room_id)
        # 予約 API の公開範囲は today〜+6 日。それより先の週は公開月間 API で補う。
        open_until = today + timedelta(days=6)
        studio = studio_repo.get_studio_by_ref(
            self._db_path, item.studio_id, item.studio_room_id
        )

        # reserve API は date_from=today 起点でないと未来日を返さない仕様。
        # 7 日窓を 1 回だけ取得して、各週でフィルタする。
        reserve_window: list[Lesson] | None = None
        # 公開月間 API は月単位でキャッシュする
        monthly_cache: dict[tuple[int, int], list[Lesson]] = {}
        reservations = reservation_repo.list_reservations(
            self._db_path, since=today
        )
        reservation_index = {
            (r.lesson_date, r.lesson_time, r.program_id): r for r in reservations
        }

        results: list[OccurrencePreview] = []
        for week in range(weeks):
            lesson_date = base + timedelta(weeks=week)
            # schedule_open_days=7 は「今日〜今日+6 日」の 7 日間を開放する仕様。
            # 逆算で lesson_date の予約実行は lesson_date - 6 日の 9:00。
            run_at = datetime.combine(
                lesson_date - timedelta(days=6),
                time(run_hour, run_minute),
            )
            week_start = lesson_date - timedelta(days=lesson_date.weekday())
            week_end = week_start + timedelta(days=6)
            if lesson_date <= open_until:
                # reserve API 窓内: today 起点 7 日を 1 回取得してフィルタ
                if reserve_window is None:
                    try:
                        reserve_window = self._gateway.fetch_week(studio_ref, today)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "occurrence preview: reserve window fetch failed: %s",
                            exc,
                        )
                        reserve_window = []
                lessons = [
                    ls for ls in reserve_window
                    if week_start <= ls.lesson_date <= week_end
                ]
            else:
                # 予約開放外の週は公開月間 API から
                lessons = self._load_week_lessons(
                    studio=studio,
                    studio_ref=studio_ref,
                    week_start=week_start,
                    out_of_range=True,
                    monthly_cache=monthly_cache,
                )

            match = self._match_lesson(lessons, lesson_date=lesson_date, item=item)
            logger.info(
                "preview match: date=%s item.pid=%s item.name=%s lessons_count=%d matched=%s matched_name=%s",
                lesson_date, item.program_id, item.program_name,
                sum(1 for ls in lessons if ls.lesson_date == lesson_date),
                match is not None,
                match.program_name if match else None,
            )
            reservation = reservation_index.get((lesson_date, item.start_time, item.program_id))

            diff_flags: list[str] = []
            alert_message: str | None = None
            instructor_name: str | None = match.instructor_name if match else None
            actual_time = match.start_time if match else item.start_time
            # schedule が未公開の週（lessons が空 or 該当日に 1 件も無い）は
            # 登録名で補完せず program_name=None にして UI で「未定」表示にする。
            same_day_count = sum(
                1 for ls in lessons if ls.lesson_date == lesson_date
            )
            program_name: str | None
            if match is not None:
                program_name = match.program_name
            elif same_day_count == 0:
                program_name = None
            else:
                program_name = item.program_name

            if match is not None and match.start_time != item.start_time:
                diff_flags.append("時間変更")
                alert_message = f"時間が変更されています（{item.start_time} → {match.start_time}）"
            if match is not None and match.program_name != item.program_name:
                diff_flags.append("プログラム変更")

            status: str
            if reservation is not None:
                status = "reserved"
            elif match is None:
                status = "planned"
            elif diff_flags:
                status = "attention"
            elif run_at.date() <= today:
                status = "waiting"
            else:
                status = "planned"

            results.append(
                OccurrencePreview(
                    lesson_date=lesson_date,
                    lesson_time=actual_time,
                    program_name=program_name,
                    instructor_name=instructor_name,
                    scheduled_run_at=run_at,
                    status=status,  # type: ignore[arg-type]
                    seat_no=reservation.seat_no if reservation else None,
                    diff_flags=diff_flags,
                    alert_message=alert_message,
                )
            )
        return results

    # --- 実行 -----------------------------------------------------

    def execute_all_for_today(
        self,
        *,
        today: date,
        target_date: date | None = None,
        source_category: HistoryCategory = HistoryCategory.AUTOMATION,
    ) -> list[tuple[RecurringReservation, RecurringRunResult]]:
        """本日 9:00 に実行すべきアクティブ定期予約をまとめて実行する。

        ``target_date`` が指定された場合は、その日付に該当する曜日の定期予約のみを
        実行する（= ``target_date`` が新規解放された日に絞る用途）。未指定時は
        ``today + 6`` 日（= schedule_open_days=7 仕様での新規解放日）を target として拾う。
        戻り値は (対象定期予約, 実行結果) のタプル列。呼び出し側が通知メッセージを
        組み立てるために定期予約のメタ情報（program_name, seat_preferences 等）を
        必要とするため、結果だけでなく item も返す。
        """

        resolved_target = (
            target_date if target_date is not None else today + timedelta(days=6)
        )
        items = self.list_active()
        targets = [i for i in items if i.day_of_week == resolved_target.weekday()]
        results: list[tuple[RecurringReservation, RecurringRunResult]] = []
        for item in targets:
            result = self.execute_one(
                item.id,
                target_date=resolved_target,
                source_category=source_category,
            )
            results.append((item, result))
        return results

    def execute_one(
        self,
        recurring_id: str,
        *,
        target_date: date,
        source_category: HistoryCategory = HistoryCategory.AUTOMATION,
    ) -> RecurringRunResult:
        item = self.get(recurring_id)
        if item is None:
            raise NotFound(f"recurring {recurring_id} not found")
        if item.status is not RecurringStatus.ACTIVE:
            return RecurringRunResult(
                recurring_id=recurring_id,
                target_date=target_date,
                ok=False,
                seat_no=None,
                external_id=None,
                message="定期予約が有効ではありません",
                failure_reason="NotActive",
            )

        # 冪等性: 同日に成功済みならスキップ
        already = self._already_reserved(item, target_date)
        if already is not None:
            return RecurringRunResult(
                recurring_id=recurring_id,
                target_date=target_date,
                ok=True,
                seat_no=already.seat_no,
                external_id=already.external_id,
                message="既に予約済みです",
            )

        lesson = self._resolve_target_lesson(item, target_date)
        if lesson is None:
            message = "対象レッスンを見つけられませんでした"
            self._record_history(
                item,
                target_date=target_date,
                result=HistoryResult.FAILURE,
                message=message,
                attempted_seats=item.seat_preferences,
                seat_no=None,
                failure_reason="LessonNotFound",
                category=source_category,
            )
            return RecurringRunResult(
                recurring_id=recurring_id,
                target_date=target_date,
                ok=False,
                seat_no=None,
                external_id=None,
                message=message,
                failure_reason="LessonNotFound",
            )

        started = datetime.now()
        attempt = self._gateway.attempt_reservation(
            studio_lesson_id=lesson.studio_lesson_id,
            no_preferences=item.seat_preferences,
        )
        elapsed_ms = int((datetime.now() - started).total_seconds() * 1000)

        if attempt.ok and attempt.external_id is not None:
            reservation = Reservation(
                id=uuid.uuid4().hex,
                external_id=attempt.external_id,
                studio_lesson_id=lesson.studio_lesson_id,
                lesson_date=lesson.lesson_date,
                lesson_time=lesson.start_time,
                program_id=lesson.program_id,
                program_name=lesson.program_name,
                instructor_name=lesson.instructor_name,
                studio_id=lesson.studio_id,
                studio_room_id=lesson.studio_room_id,
                seat_no=attempt.seat_no,
                origin=ReservationOrigin.RECURRING,
                origin_id=item.id,
                status=ReservationStatus.CONFIRMED,
            )
            reservation_repo.upsert_reservation(self._db_path, reservation)
            result = (
                HistoryResult.SUCCESS
                if attempt.succeeded_with_first_choice
                else HistoryResult.WARNING
            )
            message = attempt.message or (
                f"{lesson.program_name} を席 {attempt.seat_no:02d} で予約しました"
            )
        else:
            result = HistoryResult.FAILURE
            message = attempt.message or "予約に失敗しました"

        self._record_history(
            item,
            target_date=target_date,
            result=result,
            message=message,
            attempted_seats=item.seat_preferences,
            seat_no=attempt.seat_no,
            failure_reason=attempt.failure_reason,
            elapsed_ms=elapsed_ms,
            category=source_category,
            studio_lesson_id=lesson.studio_lesson_id,
        )

        return RecurringRunResult(
            recurring_id=recurring_id,
            target_date=target_date,
            ok=attempt.ok,
            seat_no=attempt.seat_no,
            external_id=attempt.external_id,
            message=message,
            failure_reason=attempt.failure_reason,
        )

    # --- 内部 ------------------------------------------------------

    def _already_reserved(
        self,
        item: RecurringReservation,
        target_date: date,
    ) -> Reservation | None:
        reservations = reservation_repo.list_reservations(
            self._db_path, since=target_date
        )
        for r in reservations:
            if (
                r.lesson_date == target_date
                and r.program_id == item.program_id
                and r.studio_id == item.studio_id
                and r.studio_room_id == item.studio_room_id
                and r.status is ReservationStatus.CONFIRMED
            ):
                return r
        return None

    def _resolve_target_lesson(
        self,
        item: RecurringReservation,
        target_date: date,
    ) -> Lesson | None:
        week_start = target_date - timedelta(days=target_date.weekday())
        try:
            lessons = self._gateway.fetch_week(
                StudioRef(item.studio_id, item.studio_room_id),
                week_start,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("failed to fetch schedule for recurring run: %s", exc)
            return None
        return self._match_lesson(lessons, lesson_date=target_date, item=item)

    def _load_week_lessons(
        self,
        *,
        studio: Studio | None,
        studio_ref: StudioRef,
        week_start: date,
        out_of_range: bool,
        monthly_cache: dict[tuple[int, int], list[Lesson]],
    ) -> list[Lesson]:
        """プレビュー用の週次レッスンを取得する。

        予約 API の公開範囲（today + 6 日以内）は reserve API から、それ以降は
        公開月間 API から取得する。公開月間 API は月単位のため `monthly_cache`
        で同じ月の重複呼び出しを防ぐ。どちらの経路も `instructor_name` を埋めて
        返すので、`OccurrencePreview` の担当者が schedule から補完できる。
        """

        if not out_of_range:
            try:
                return self._gateway.fetch_week(studio_ref, week_start)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "occurrence preview: failed to fetch week=%s: %s",
                    week_start, exc,
                )
                return []

        # 公開月間 API 経由（予約不可の週でも instructor を埋めたい）
        if studio is None or not studio.club_code or not studio.sisetcd:
            return []
        week_end = week_start + timedelta(days=6)
        months: set[tuple[int, int]] = set()
        cursor = week_start
        while cursor <= week_end:
            months.add((cursor.year, cursor.month))
            cursor += timedelta(days=1)
        collected: list[Lesson] = []
        for year, month in sorted(months):
            if (year, month) not in monthly_cache:
                try:
                    monthly_cache[(year, month)] = (
                        self._gateway.fetch_monthly_public(  # type: ignore[attr-defined]
                            club_code=studio.club_code,
                            sisetcd=studio.sisetcd,
                            studio_id=studio.studio_id,
                            studio_room_id=studio.studio_room_id,
                            year=year,
                            month=month,
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "occurrence preview: public monthly fetch failed "
                        "year=%d month=%d: %s",
                        year, month, exc,
                    )
                    monthly_cache[(year, month)] = []
            for lesson in monthly_cache[(year, month)]:
                if week_start <= lesson.lesson_date <= week_end:
                    collected.append(lesson)
        return collected

    def _match_lesson(
        self,
        lessons: list[Lesson],
        *,
        lesson_date: date,
        item: RecurringReservation,
    ) -> Lesson | None:
        same_day = [lsn for lsn in lessons if lsn.lesson_date == lesson_date]
        # 完全一致（時刻 + program_id）を最優先
        for lsn in same_day:
            if lsn.start_time == item.start_time and lsn.program_id == item.program_id:
                return lsn
        # program_id だけ一致（時間変更のケース）
        for lsn in same_day:
            if lsn.program_id == item.program_id:
                return lsn
        # 公開月間 API 経由では program_id の体系が異なる可能性があるため、
        # 時刻 + プログラム名でのフォールバックを用意する（instructor 補完目的）
        for lsn in same_day:
            if lsn.start_time == item.start_time and lsn.program_name == item.program_name:
                return lsn
        # 最後の救済: 同じ曜日・同じ時刻に並ぶレッスンが 1 本しかなければ、
        # CS Live 系の週替わりサブプログラム（ARMS & BACKLINE / LEG LINE 等）で
        # program_id/name ともに一致しなくても同一枠として扱う。複数候補がある
        # 場合は誤一致を避けて諦める。
        same_time = [lsn for lsn in same_day if lsn.start_time == item.start_time]
        if len(same_time) == 1:
            return same_time[0]
        return None

    def _record_history(
        self,
        item: RecurringReservation,
        *,
        target_date: date,
        result: HistoryResult,
        message: str,
        attempted_seats: list[int],
        seat_no: int | None,
        failure_reason: str | None,
        category: HistoryCategory = HistoryCategory.AUTOMATION,
        elapsed_ms: int | None = None,
        studio_lesson_id: int | None = None,
    ) -> None:
        history_repo.insert(
            self._db_path,
            HistoryEntry(
                id=None,
                request_id=uuid.uuid4().hex[:12],
                occurred_at=datetime.now(),
                category=category,
                endpoint="reservation.reserve",
                elapsed_ms=elapsed_ms,
                result=result,
                message=message,
                metadata={
                    "recurring_id": item.id,
                    "program_id": item.program_id,
                    "program_name": item.program_name,
                    "lesson_date": target_date.isoformat(),
                    "lesson_time": item.start_time,
                    "seat_no": seat_no,
                    "attempted_seats": attempted_seats,
                    "origin": ReservationOrigin.RECURRING.value,
                    "studio_lesson_id": studio_lesson_id,
                    "failure_reason": failure_reason,
                },
            ),
        )
