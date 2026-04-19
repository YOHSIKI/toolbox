"""ドメインエンティティと値オブジェクト。

- framework / infra に依存しない純粋な Python データ
- 日付や時刻は `datetime.date` / `datetime.datetime` で持つ
- 座席番号 (`no`) は hacomono の API 定義に合わせて `int`
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from enum import Enum
from typing import Literal

DAY_OF_WEEK_LABEL = ["月", "火", "水", "木", "金", "土", "日"]


# ------------------------------------------------------------
# 列挙
# ------------------------------------------------------------
class RecurringStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    DELETED = "deleted"


class ReservationStatus(str, Enum):
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ReservationOrigin(str, Enum):
    """予約の由来。単発予約から作ったか、定期予約から自動で取られたか。"""

    SINGLE = "single"
    RECURRING = "recurring"


class LessonState(str, Enum):
    """カレンダー表示のための状態分類。"""

    AVAILABLE = "available"
    RESERVED = "reserved"
    TARGET = "target"
    FULL = "full"
    UNRESERVABLE = "unreservable"


# ------------------------------------------------------------
# 値オブジェクト
# ------------------------------------------------------------
@dataclass(slots=True, frozen=True)
class StudioRef:
    """店舗と部屋の識別子の組。"""

    studio_id: int
    studio_room_id: int

    def as_tuple(self) -> tuple[int, int]:
        return (self.studio_id, self.studio_room_id)


@dataclass(slots=True)
class Studio:
    """店舗マスター。UI のプルダウンや既定値に使う。"""

    id: int
    studio_id: int
    studio_room_id: int
    display_name: str
    club_code: str | None = None
    sisetcd: str | None = None
    is_default: bool = False

    @property
    def ref(self) -> StudioRef:
        return StudioRef(self.studio_id, self.studio_room_id)


@dataclass(slots=True)
class Lesson:
    """スケジュール上の 1 レッスン。"""

    studio_lesson_id: int
    studio_id: int
    studio_room_id: int
    lesson_date: date
    start_time: str  # "HH:MM"
    end_time: str | None
    program_id: str
    program_name: str
    instructor_id: int | None
    instructor_name: str | None
    capacity: int | None
    remaining_seats: int | None
    studio_room_space_id: int | None
    space_layout_name: str | None
    is_reservable: bool
    reservable_from: datetime | None
    reservable_to: datetime | None
    state: LessonState = LessonState.AVAILABLE
    reserved_seat_no: int | None = None
    reserved_origin: ReservationOrigin | None = None
    reserved_reservation_id: str | None = None

    @property
    def is_full(self) -> bool:
        if self.remaining_seats is not None:
            return self.remaining_seats <= 0
        return False

    @property
    def weekday(self) -> int:
        return self.lesson_date.weekday()


@dataclass(slots=True)
class SeatMap:
    """あるレッスンの座席埋まり状況。"""

    studio_lesson_id: int
    capacity: int
    taken_nos: list[int]

    @property
    def available_nos(self) -> list[int]:
        taken = set(self.taken_nos)
        return [n for n in range(1, self.capacity + 1) if n not in taken]


@dataclass(slots=True)
class RecurringReservation:
    """毎週の定期予約ルール。"""

    id: str
    day_of_week: int  # 0=月 ... 6=日
    start_time: str  # "HH:MM"
    program_id: str
    program_name: str
    studio_id: int
    studio_room_id: int
    seat_preferences: list[int] = field(default_factory=list)
    status: RecurringStatus = RecurringStatus.ACTIVE
    note: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def weekday_label(self) -> str:
        if 0 <= self.day_of_week < len(DAY_OF_WEEK_LABEL):
            return DAY_OF_WEEK_LABEL[self.day_of_week]
        return "?"

    @property
    def headline(self) -> str:
        return f"毎週 {self.weekday_label}曜 {self.start_time} {self.program_name}"

    def seat_preferences_as_labels(self) -> list[str]:
        return [f"{no:02d}" for no in self.seat_preferences]


@dataclass(slots=True)
class Reservation:
    """予約のローカル記録。hacomono の予約 1 件に対応する。"""

    id: str
    studio_lesson_id: int
    lesson_date: date
    lesson_time: str
    program_id: str
    program_name: str
    studio_id: int
    studio_room_id: int
    external_id: int | None = None
    instructor_name: str | None = None
    seat_no: int | None = None
    origin: ReservationOrigin = ReservationOrigin.SINGLE
    origin_id: str | None = None
    status: ReservationStatus = ReservationStatus.CONFIRMED
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def seat_label(self) -> str:
        return f"{self.seat_no:02d}" if self.seat_no is not None else "-"

    @property
    def datetime_start(self) -> datetime:
        hh, mm = self.lesson_time.split(":")
        return datetime.combine(self.lesson_date, time(int(hh), int(mm)))


@dataclass(slots=True)
class ReservationAttempt:
    """予約 POST の結果。成功時は external_id と seat_no、失敗時は理由を持つ。"""

    ok: bool
    studio_lesson_id: int
    attempted_preferences: list[int]
    seat_no: int | None = None
    external_id: int | None = None
    message: str = ""
    failure_reason: str | None = None  # 例: "SeatUnavailable"

    @property
    def succeeded_with_first_choice(self) -> bool:
        return bool(
            self.ok
            and self.seat_no is not None
            and self.attempted_preferences
            and self.attempted_preferences[0] == self.seat_no
        )


# ------------------------------------------------------------
# 予約実行履歴（サマリや Dashboard 用、永続 `history` テーブルと別概念）
# ------------------------------------------------------------
class HistoryCategory(str, Enum):
    AUTOMATION = "automation"
    MANUAL = "manual"
    LOGIN = "login"
    MONTHLY_SYNC = "monthly_sync"
    WEEKLY_SYNC = "weekly_sync"
    SYNC_MY_RESERVATIONS = "sync_my_reservations"
    NOTIFICATION = "notification"
    SYSTEM = "system"


class HistoryResult(str, Enum):
    SUCCESS = "success"
    WARNING = "warning"
    FAILURE = "failure"


@dataclass(slots=True)
class HistoryEntry:
    id: int | None
    request_id: str
    occurred_at: datetime
    category: HistoryCategory
    result: HistoryResult
    endpoint: str | None = None
    elapsed_ms: int | None = None
    message: str | None = None
    metadata: dict | None = None


# ------------------------------------------------------------
# Dashboard 用 DTO
# ------------------------------------------------------------
@dataclass(slots=True)
class DailySummaryItem:
    program_name: str
    seat_no: int | None
    lesson_date: date
    lesson_time: str
    result: HistoryResult
    detail: str


@dataclass(slots=True)
class DailySummary:
    date: date
    success_count: int
    warning_count: int
    failure_count: int
    items: list[DailySummaryItem] = field(default_factory=list)

    @property
    def total_count(self) -> int:
        return self.success_count + self.warning_count + self.failure_count

    @property
    def overall_level(self) -> HistoryResult:
        if self.failure_count > 0:
            return HistoryResult.FAILURE
        if self.warning_count > 0:
            return HistoryResult.WARNING
        return HistoryResult.SUCCESS


@dataclass(slots=True)
class UpcomingReservation:
    recurring_id: str
    lesson_date: date
    lesson_time: str
    program_name: str
    seat_preferences: list[int]
    scheduled_run_at: datetime


@dataclass(slots=True)
class OccurrencePreview:
    lesson_date: date
    lesson_time: str
    program_name: str
    instructor_name: str | None
    scheduled_run_at: datetime
    status: Literal["reserved", "waiting", "planned", "attention"]
    seat_no: int | None = None
    diff_flags: list[str] = field(default_factory=list)
    alert_message: str | None = None


# ------------------------------------------------------------
# カレンダー用 DTO
# ------------------------------------------------------------
@dataclass(slots=True)
class CalendarCell:
    hour: int
    weekday: int
    lessons: list[Lesson] = field(default_factory=list)

    @property
    def has_lessons(self) -> bool:
        return bool(self.lessons)


@dataclass(slots=True)
class CalendarWeek:
    studio: Studio
    week_start: date
    week_end: date
    days: list[date]
    hours: list[int]
    rows: list[list[CalendarCell]] = field(default_factory=list)
    selected_lesson: Lesson | None = None
    selected_seat_map: SeatMap | None = None
    today: date | None = None
    # 予約開放範囲（today + open_days - 1 まで）を超えた週は公開月間 API から閲覧専用で表示
    open_days: int = 7
    out_of_range: bool = False
    open_until: date | None = None


# ------------------------------------------------------------
# 便利関数（日付処理）
# ------------------------------------------------------------
def monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def next_weekday(from_day: date, weekday: int) -> date:
    delta = (weekday - from_day.weekday()) % 7
    return from_day + timedelta(days=delta)
