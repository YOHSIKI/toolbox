"""`ReservationGateway` の実装。

- `HacomonoClient` を注入で受け取り、生レスポンスをドメインエンティティに変換
- dry-run モード時は書き込み系 API（reserve/cancel/move）を擬似成功に差し替え
- 予約試行は希望席の第 1 候補から順に投げ、`SeatUnavailable` なら次を試す
- `AlreadyReserved` / `CapacityExceeded` / `OutsideReservationWindow` は失敗として即時終了
"""

from __future__ import annotations

import logging
import re as _re
import time as _time
import unicodedata as _unicodedata
from datetime import date, timedelta
from pathlib import Path

from app.adapters.public_monthly_mapper import map_public_monthly
from app.adapters.schedule_mapper import (
    build_space_index,
    map_my_reservations,
    map_reservation_result,
    map_reserved_nos,
    map_weekly_schedule,
)
from app.domain.entities import (
    Lesson,
    Reservation,
    ReservationAttempt,
    SeatMap,
    SeatPosition,
    StudioRef,
)
from app.utils.program_similarity import similarity_ensemble
from infra.hacomono.auth import AuthSession
from infra.hacomono.client import HacomonoClient
from infra.hacomono.errors import (
    AlreadyReserved,
    BusinessRuleViolation,
    CapacityExceeded,
    HacomonoError,
    OutsideReservationWindow,
    SeatUnavailable,
)
from infra.hacomono.public_monthly import PublicMonthlyClient

try:
    from db.repositories import space_repo
except Exception:  # noqa: BLE001 - テスト等で db が無くても import を止めない
    space_repo = None  # type: ignore[assignment]

try:
    from db.repositories import observed_lesson_repo
except Exception:  # noqa: BLE001 - テスト等で db が無くても import を止めない
    observed_lesson_repo = None  # type: ignore[assignment]

try:
    from db.repositories import program_alias_repo
except Exception:  # noqa: BLE001 - テスト等で db が無くても import を止めない
    program_alias_repo = None  # type: ignore[assignment]

try:
    from db.repositories import studio_repo
except Exception:  # noqa: BLE001 - テスト等で db が無くても import を止めない
    studio_repo = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


_NAME_NOISE_RE = _re.compile(r"[\s/／・、,.。．\-－_]+")


# --- alias 自動学習の類似度ゲート閾値 ------------------------------------
#
# 旧 alias と新観測名の中央値類似度で判定する。hacomono 側でプログラムが
# 差し替えられた時の誤学習を防ぐフェイルセーフ。
#
#   median >= alias_sim_accept           → 通常 upsert（表記揺れとみなす）
#   alias_sim_warn ≤ median < accept     → warning ログを出して upsert
#   median < alias_sim_warn              → error ログを出してスキップ
#
# 閾値の実体は `config.settings.Settings.alias_sim_accept` /
# `alias_sim_warn` に統合された。ゲートウェイは `__init__` で値を受け取る。
# dashboard_query の program_changes 通知抑制でも同じ settings 値を使う
# （single source of truth）。
#
# Issue #1 の 16 ペア実測（2026-04-20, 5 指標に拡張）:
#   同一群 median min = 0.452（AA747 系プレフィクスペア）
#   別物群 median max = 0.545（フィールヨガ / パワーヨガ）
# デフォルトの accept=0.60 / warn=0.40 は上記の下限を参考にしたキャリブ値。


def _normalize_program_name(name: str | None) -> str:
    """プログラム名をマッチング用に正規化する。

    reserve API は全角カナ（例: "シェイプパンプ"）、公開月間 API は
    半角カナ（例: "ｼｪｲﾌﾟﾊﾟﾝﾌﾟ"）や記号混在で返すことがある。NFKC で
    全角・半角・記号を揃え、空白/スラッシュ/ドット等の区切りを除去した
    上で lower case にすることで、表記ゆれに強いキーを作る。
    """

    if not name:
        return ""
    s = _unicodedata.normalize("NFKC", str(name))
    s = _NAME_NOISE_RE.sub("", s)
    return s.strip().lower()


class HacomonoGateway:
    """ReservationGateway の本番実装。"""

    def __init__(
        self,
        *,
        client: HacomonoClient,
        auth: AuthSession,
        dry_run: bool,
        public_client: PublicMonthlyClient | None = None,
        db_path: Path | None = None,
        alias_sim_accept: float = 0.60,
        alias_sim_warn: float = 0.40,
    ) -> None:
        self._client = client
        self._auth = auth
        self._dry_run = dry_run
        self._public_client = public_client
        self._db_path = db_path
        self._alias_sim_accept = alias_sim_accept
        self._alias_sim_warn = alias_sim_warn
        # schedule API から得た studio_room_space_id → (SeatPosition[], grid_cols, grid_rows) の
        # インメモリキャッシュ。起動時に DB から復元され、fetch_week で更新される。
        # fetch_seat_map / 公開月間 API 補完の両方から参照される。
        self._space_index: dict[int, list[SeatPosition]] = {}
        self._space_grid: dict[int, tuple[int, int]] = {}  # space_id → (cols, rows)
        # 学習 hint 索引。優先順位:
        #   1. _hint_full:   (studio_key, normalized_name, weekday, "HH:MM") → space_id （最も精密）
        #   2. _hint_name:   (studio_key, normalized_name) → space_id         （同名プログラムのどれか）
        #   3. _hint_time:   (studio_key, weekday, "HH:MM") → space_id        （曜日×時刻ベース、CS Live 系救済）
        # studio_key = (studio_id, studio_room_id)
        self._hint_full: dict[tuple[int, int, str, int, str], int] = {}
        self._hint_name: dict[tuple[int, int, str], int] = {}
        self._hint_time: dict[tuple[int, int, int, str], int] = {}
        # progcd → (program_id, program_name) の in-memory エイリアスキャッシュ。
        # (studio_id, studio_room_id) で店舗別に分けて保持。起動時に DB から
        # 全店舗分を復元、fetch_monthly_public で observed と merge した結果を
        # 元に学習する。未観測の公開月間 lesson の表記置換にも使う。
        self._program_alias: dict[tuple[int, int], dict[str, dict]] = {}
        # 外部 API 呼び出しのキャッシュ。
        # TTL 24 時間: 時間割の更新は深夜 0:05 の cache_refresh_job と朝 9:00 の
        # run_at_nine_job（予約実行後）の 2 回だけと割り切る運用。日中に TTL 切れで
        # 外部 API を叩くことを避け、画面切替のたびの 500ms〜1s の cold 遅延を無くす。
        # 実際の鮮度は invalidate_caches() の明示呼び出しで担保。
        self._cache_ttl = 86400.0
        self._week_cache: dict[
            tuple[int, int, str, int], tuple[float, list[Lesson]]
        ] = {}
        self._monthly_cache: dict[
            tuple[str, str, int, int, str],
            tuple[float, list[Lesson]],
        ] = {}
        # 公開月間 API から取得した休館日（date の集合）を月単位でキャッシュ。
        # 全画面で毎リクエスト使うため、cache miss すると体感 300ms+ 遅い。
        # キーに kind を含めて「定休日のみ」「祝日特別日のみ」を区別する。
        self._closed_days_cache: dict[
            tuple[str, str, int, int, str], tuple[float, set[date]]
        ] = {}
        # DB から学習結果を復元（コンテナ再起動でも maps は失われない）
        self._load_persisted()

    # --- キャッシュ制御 -------------------------------------

    def invalidate_caches(self) -> None:
        """week / monthly / closed_days キャッシュを全てクリアする。

        ユーザーが「マイページと同期」ボタンを押した時や、予約/取消/席変更の
        直後に呼び出し、次の画面描画で必ず最新データを取得させる。
        学習済みの座席配置・hint は in-memory & DB に残るため、読込速度は
        落ちない。
        """

        self._week_cache.clear()
        self._monthly_cache.clear()
        self._closed_days_cache.clear()

    def fetch_closed_days(
        self,
        *,
        club_code: str,
        sisetcd: str,
        year: int,
        month: int,
        kind: str = "fixed",
    ) -> set[date]:
        """指定月の休館日集合を公開月間 API から取得する（キャッシュ付き）。

        calendar_query から呼ばれ、毎リクエストで API を叩かないようにする。

        `kind` は `collect_closed_dates` にそのまま渡す:
          - `"fixed"` （既定）→ datekb=1 のみ（確定休館）
          - `"special"` → datekb=3 のみ（祝日特別日）
          - `"all"` → 従来どおり全て
        """

        from app.adapters.public_monthly_mapper import collect_closed_dates

        if self._public_client is None:
            return set()
        cache_key = (club_code, sisetcd, year, month, kind)
        now = _time.monotonic()
        cached = self._closed_days_cache.get(cache_key)
        if cached is not None and (now - cached[0]) < self._cache_ttl:
            return cached[1]
        try:
            payload = self._public_client.fetch(
                club_code=club_code, year_month=f"{year:04d}{month:02d}"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "fetch_closed_days failed club=%s ym=%04d%02d kind=%s: %s",
                club_code, year, month, kind, exc,
            )
            return set()
        mapper_kind = kind if kind in ("fixed", "special") else None
        dates = collect_closed_dates(
            payload, year=year, month=month, kind=mapper_kind
        )
        self._closed_days_cache[cache_key] = (now, dates)
        return dates

    # --- 永続化 ---------------------------------------------

    def _load_persisted(self) -> None:
        """起動時に DB から space 配置と learning hints を in-memory に復元する。"""

        if self._db_path is None or space_repo is None:
            return
        try:
            details = space_repo.list_space_details(self._db_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("load persisted space_details failed: %s", exc)
            details = {}
        for sid, entry in details.items():
            self._space_index[sid] = entry["positions"]
            self._space_grid[sid] = (entry["grid_cols"], entry["grid_rows"])
        try:
            rows = space_repo.list_layout_hints(self._db_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("load persisted layout_hints failed: %s", exc)
            rows = []
        for row in rows:
            sid_studio = int(row["studio_id"])
            sid_room = int(row["studio_room_id"])
            name_norm = str(row["program_name_norm"] or "")
            wd = int(row["day_of_week"])
            t = str(row["start_time"] or "")
            space_id = int(row["studio_room_space_id"])
            if name_norm:
                self._hint_full[(sid_studio, sid_room, name_norm, wd, t)] = space_id
                self._hint_name[(sid_studio, sid_room, name_norm)] = space_id
            self._hint_time[(sid_studio, sid_room, wd, t)] = space_id
        # program_aliases を全店舗分 load（店舗数は少数前提なので起動時に一括）
        if program_alias_repo is not None and studio_repo is not None:
            try:
                for studio in studio_repo.list_studios(self._db_path):
                    key = (int(studio.studio_id), int(studio.studio_room_id))
                    self._program_alias[key] = program_alias_repo.list_aliases(
                        self._db_path,
                        studio_id=studio.studio_id,
                        studio_room_id=studio.studio_room_id,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("load program_aliases failed: %s", exc)
        logger.info(
            "gateway loaded persisted learning: spaces=%d hints_full=%d hints_name=%d hints_time=%d aliases=%d",
            len(self._space_index), len(self._hint_full),
            len(self._hint_name), len(self._hint_time),
            sum(len(v) for v in self._program_alias.values()),
        )

    def _persist_space(self, space_id: int, positions: list[SeatPosition]) -> None:
        """座席配置を DB に保存する。in-memory と同じなら書き込みをスキップ。"""

        if not positions:
            return
        cols = max((p.coord_x for p in positions), default=-1) + 1
        rows = max((p.coord_y for p in positions), default=-1) + 1
        # in-memory と既に一致していれば DB write 不要（SeatPosition は frozen
        # dataclass で等価比較可能、grid_cols/rows も dict で一致判定できる）
        if (
            self._space_index.get(space_id) == positions
            and self._space_grid.get(space_id) == (cols, rows)
        ):
            return
        self._space_grid[space_id] = (cols, rows)
        if self._db_path is None or space_repo is None:
            return
        try:
            space_repo.upsert_space_details(
                self._db_path,
                studio_room_space_id=space_id,
                name=None,
                space_num=len(positions),
                grid_cols=cols,
                grid_rows=rows,
                positions=positions,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("persist space_details failed (sid=%d): %s", space_id, exc)

    def _persist_hint(
        self,
        *,
        studio_id: int,
        studio_room_id: int,
        program_name: str | None,
        day_of_week: int,
        start_time: str,
        space_id: int,
    ) -> None:
        """学習 hint を in-memory index に保存し、変更があれば DB にも書く。

        同じキーで既に同じ space_id が in-memory にあれば DB write をスキップする。
        fetch_week のたびに 40+ 件の hint を毎回書き込むのを避けて、NAS 上の
        SQLite の書き込みコストを抑える。
        """

        name_norm = _normalize_program_name(program_name)
        full_key = (studio_id, studio_room_id, name_norm, day_of_week, start_time) if name_norm else None
        name_key = (studio_id, studio_room_id, name_norm) if name_norm else None
        time_key = (studio_id, studio_room_id, day_of_week, start_time)

        # 既存の in-memory 索引と比較して、変更が無ければ DB write をスキップ
        unchanged = (
            (full_key is None or self._hint_full.get(full_key) == space_id)
            and (name_key is None or self._hint_name.get(name_key) == space_id)
            and self._hint_time.get(time_key) == space_id
        )

        # in-memory は常に最新値を反映
        self._hint_time[time_key] = space_id
        if full_key is not None:
            self._hint_full[full_key] = space_id
        if name_key is not None:
            self._hint_name[name_key] = space_id

        if unchanged:
            return
        if self._db_path is None or space_repo is None:
            return
        try:
            space_repo.upsert_layout_hint(
                self._db_path,
                studio_id=studio_id,
                studio_room_id=studio_room_id,
                program_name_norm=name_norm,
                program_name_raw=program_name,
                day_of_week=day_of_week,
                start_time=start_time,
                studio_room_space_id=space_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "persist layout hint failed (pname=%r wd=%d t=%s): %s",
                program_name, day_of_week, start_time, exc,
            )

    def _lookup_hint(
        self,
        *,
        studio_id: int,
        studio_room_id: int,
        program_name: str | None,
        day_of_week: int,
        start_time: str,
    ) -> int | None:
        """3 段階 fallback で space_id を引く。"""

        name_norm = _normalize_program_name(program_name)
        if name_norm:
            v = self._hint_full.get((studio_id, studio_room_id, name_norm, day_of_week, start_time))
            if v is not None:
                return v
            v = self._hint_name.get((studio_id, studio_room_id, name_norm))
            if v is not None:
                return v
        return self._hint_time.get((studio_id, studio_room_id, day_of_week, start_time))

    @property
    def dry_run(self) -> bool:
        return self._dry_run

    # --- 認証 -----------------------------------------------

    def ensure_authenticated(self) -> None:
        if not self._auth.is_authenticated:
            self._auth.sign_in()

    # --- 読み取り系（常に実 API） --------------------------

    def fetch_week(
        self,
        studio: StudioRef,
        week_start: date,
        days: int = 7,
    ) -> list[Lesson]:
        cache_key = (
            studio.studio_id,
            studio.studio_room_id,
            week_start.isoformat(),
            max(days, 1),
        )
        now = _time.monotonic()
        cached = self._week_cache.get(cache_key)
        if cached is not None and (now - cached[0]) < self._cache_ttl:
            return cached[1]
        date_from = week_start.isoformat()
        date_to = (week_start + timedelta(days=max(days, 1) - 1)).isoformat()
        response = self._client.fetch_schedule(
            studio_id=studio.studio_id,
            studio_room_id=studio.studio_room_id,
            date_from=date_from,
            date_to=date_to,
        )
        # space_details を studio_room_space_id 毎にキャッシュ + DB に永続化。
        # 同じ週の fetch_seat_map がこの辞書から positions を引く。
        try:
            fresh_index = build_space_index(response.payload)
        except Exception as exc:  # noqa: BLE001 - 配置取得失敗で週表示を落とさない
            logger.warning("build_space_index failed: %s", exc)
            fresh_index = {}
        for sid, positions in fresh_index.items():
            self._space_index[sid] = positions
            self._persist_space(sid, positions)
        lessons = map_weekly_schedule(
            response.payload,
            studio_id=studio.studio_id,
            studio_room_id=studio.studio_room_id,
        )
        # 学習 hint を積み上げる。3 段階索引を全て更新し DB に永続化。
        for lesson in lessons:
            if lesson.studio_room_space_id is None:
                continue
            self._persist_hint(
                studio_id=studio.studio_id,
                studio_room_id=studio.studio_room_id,
                program_name=lesson.program_name,
                day_of_week=lesson.lesson_date.weekday(),
                start_time=lesson.start_time,
                space_id=lesson.studio_room_space_id,
            )
        # reserve API で観測したレッスン情報を DB に蓄積する。これは後に
        # 公開月間 API 経由の lesson（別名/揺れた表記）を表示する際に、
        # 「実際に開催された/される表記」で上書き表示する元データになる。
        if self._db_path is not None and observed_lesson_repo is not None:
            try:
                observed_lesson_repo.upsert_many(self._db_path, lessons)
            except Exception as exc:  # noqa: BLE001
                logger.warning("observed_lessons upsert failed: %s", exc)
        self._week_cache[cache_key] = (now, lessons)
        return lessons

    def fetch_monthly_public(
        self,
        *,
        club_code: str,
        sisetcd: str,
        studio_id: int,
        studio_room_id: int,
        year: int,
        month: int,
        week_range: tuple[date, date] | None = None,
    ) -> list[Lesson]:
        """公開月間 API から指定月のスケジュールを取得して Lesson のリストに変換。"""

        if self._public_client is None:
            logger.warning("fetch_monthly_public: public_client is None")
            return []
        yyyymm = f"{year:04d}{month:02d}"
        week_key = (
            "" if week_range is None
            else f"{week_range[0].isoformat()}:{week_range[1].isoformat()}"
        )
        cache_key = (club_code, sisetcd, studio_id, studio_room_id, yyyymm + "/" + week_key)
        now = _time.monotonic()
        cached = self._monthly_cache.get(cache_key)
        if cached is not None and (now - cached[0]) < self._cache_ttl:
            return cached[1]
        logger.info("fetch_monthly_public: calling fetch(club=%s, ym=%s)", club_code, yyyymm)
        payload = self._public_client.fetch(club_code=club_code, year_month=yyyymm)
        logger.info(
            "fetch_monthly_public: payload schedule=%d closed=%d",
            len(payload.schedule), len(payload.closed_days),
        )
        mapped = map_public_monthly(
            payload,
            year=year,
            month=month,
            sisetcd=sisetcd,
            studio_id=studio_id,
            studio_room_id=studio_room_id,
            week_range=week_range,
        )
        # 公開月間 API には studio_room_space_id が含まれないため、
        # reserve API 窓で学習した 3 段階索引で補完する。
        #   1) (program_name, weekday, time) 完全一致 — 最精密
        #   2) program_name のみ — 同名プログラムの直近学習
        #   3) (weekday, time) — プログラム名不明/表記揺れの救済
        for lesson in mapped:
            if lesson.studio_room_space_id is not None:
                continue
            space_id = self._lookup_hint(
                studio_id=studio_id,
                studio_room_id=studio_room_id,
                program_name=lesson.program_name,
                day_of_week=lesson.lesson_date.weekday(),
                start_time=lesson.start_time,
            )
            if space_id is not None:
                lesson.studio_room_space_id = space_id
        # 公開月間 API 経由の lesson は表記が揺れる（半角カナ・フルネーム instructor 等）。
        # 過去に reserve API で観測した同枠のレッスンがあれば、そちらを正として
        # program_name / instructor_name / studio_room_space_id を上書きする。
        # さらに、observed で置換された lesson の (progcd → reserve の program_name/id)
        # を program_aliases に学習して永続化する。次回以降、reserve 窓の外でも
        # alias テーブルから引いて正規表記に寄せる。
        observed_keys_replaced: set[tuple] = set()
        if (
            self._db_path is not None
            and observed_lesson_repo is not None
            and mapped
        ):
            from datetime import date as _date
            try:
                if week_range is not None:
                    range_start, range_end = week_range
                else:
                    dates = [
                        ls.lesson_date for ls in mapped
                        if isinstance(ls.lesson_date, _date)
                    ]
                    if dates:
                        range_start, range_end = min(dates), max(dates)
                    else:
                        range_start = range_end = None
                if range_start is not None and range_end is not None:
                    observed = observed_lesson_repo.list_by_range(
                        self._db_path,
                        studio_id=studio_id,
                        studio_room_id=studio_room_id,
                        start=range_start,
                        end=range_end,
                    )
                    alias_cache = self._program_alias.setdefault(
                        (int(studio_id), int(studio_room_id)), {}
                    )
                    for lesson in mapped:
                        key = (lesson.lesson_date, lesson.start_time)
                        record = observed.get(key)
                        if record is None:
                            continue
                        # 観測値で上書き（空でなければ優先）
                        if record.get("program_name"):
                            lesson.program_name = record["program_name"]
                        if record.get("instructor_name"):
                            lesson.instructor_name = record["instructor_name"]
                        if record.get("studio_room_space_id") is not None:
                            lesson.studio_room_space_id = record[
                                "studio_room_space_id"
                            ]
                        # program_id は観測値の方が reserve 体系なので上書きする
                        # （公開月間の progcd=文字列は保持しないが、実害なし）
                        if record.get("program_id"):
                            lesson.program_id = str(record["program_id"])
                        observed_keys_replaced.add(key)
                        # progcd → reserve 表記の alias を学習。差分のみ DB 書き込み。
                        progcd = lesson.source_progcd
                        reserve_name = record.get("program_name")
                        if (
                            program_alias_repo is not None
                            and progcd
                            and reserve_name
                        ):
                            reserve_pid = record.get("program_id")
                            reserve_pid_str = (
                                str(reserve_pid) if reserve_pid is not None else None
                            )
                            existing = alias_cache.get(progcd)
                            if (
                                existing is None
                                or existing.get("program_name") != reserve_name
                                or (
                                    reserve_pid_str is not None
                                    and existing.get("program_id") != reserve_pid_str
                                )
                            ):
                                # 類似度ゲート: 既存 alias の旧名と新観測名が
                                # 大きく食い違う場合、hacomono 側でプログラム
                                # 差し替えが起きた可能性がある。upsert を
                                # 弾いて誤学習を防ぐ。初回学習（existing is
                                # None）は比較対象が無いのでスルー。
                                skip_due_to_mismatch = False
                                if existing is not None:
                                    old_name = existing.get("program_name") or ""
                                    if old_name and old_name != reserve_name:
                                        scores = similarity_ensemble(
                                            reserve_name, old_name
                                        )
                                        median = scores["median"]
                                        log_line = (
                                            "alias_gate progcd=%s new_name=%r "
                                            "old_name=%r scores="
                                            "{lev:%.2f, jw:%.2f, jac:%.2f, "
                                            "partial:%.2f, token:%.2f} "
                                            "median=%.2f decision=%s"
                                        )
                                        args = (
                                            progcd,
                                            reserve_name,
                                            old_name,
                                            scores["levenshtein"],
                                            scores["jaro_winkler"],
                                            scores["jaccard3"],
                                            scores["partial_ratio"],
                                            scores["token_set_ratio"],
                                            median,
                                        )
                                        if median >= self._alias_sim_accept:
                                            logger.info(log_line, *args, "upsert")
                                        elif median >= self._alias_sim_warn:
                                            logger.warning(
                                                log_line, *args, "upsert_warning"
                                            )
                                        else:
                                            logger.error(log_line, *args, "skip")
                                            skip_due_to_mismatch = True
                                if skip_due_to_mismatch:
                                    continue
                                alias_cache[progcd] = {
                                    "program_id": reserve_pid_str
                                    or (existing.get("program_id") if existing else None),
                                    "program_name": reserve_name,
                                    "observed_at": None,
                                }
                                try:
                                    program_alias_repo.upsert_alias(
                                        self._db_path,
                                        studio_id=studio_id,
                                        studio_room_id=studio_room_id,
                                        progcd=progcd,
                                        program_id=reserve_pid_str,
                                        program_name=reserve_name,
                                    )
                                except Exception as exc:  # noqa: BLE001
                                    logger.warning(
                                        "program_aliases upsert failed (progcd=%s): %s",
                                        progcd, exc,
                                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("observed_lessons merge failed: %s", exc)

        # observed で置換されなかった lesson (= reserve 窓の外、あるいはその週の
        # reserve 観測が無い) について、progcd ベースで alias テーブルから引く。
        # alias_cache が空の場合は DB から lazy load する。
        if mapped:
            alias_cache = self._program_alias.setdefault(
                (int(studio_id), int(studio_room_id)), {}
            )
            if not alias_cache and self._db_path is not None and program_alias_repo is not None:
                try:
                    alias_cache.update(
                        program_alias_repo.list_aliases(
                            self._db_path,
                            studio_id=studio_id,
                            studio_room_id=studio_room_id,
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("lazy load program_aliases failed: %s", exc)
            for lesson in mapped:
                key = (lesson.lesson_date, lesson.start_time)
                if key in observed_keys_replaced:
                    continue  # observed で既に正規表記に置換済み
                progcd = lesson.source_progcd
                if not progcd:
                    continue
                alias = alias_cache.get(progcd)
                if alias is None:
                    continue
                alias_name = alias.get("program_name")
                alias_pid = alias.get("program_id")
                if alias_name:
                    lesson.program_name = alias_name
                if alias_pid:
                    lesson.program_id = str(alias_pid)
        logger.info("fetch_monthly_public: mapped %d lessons (sisetcd=%s)", len(mapped), sisetcd)
        self._monthly_cache[cache_key] = (now, mapped)
        return mapped

    def fetch_my_reservations(self) -> list[Reservation]:
        response = self._client.list_my_reservations()
        payload = response.payload
        if isinstance(payload, dict):
            top_keys = sorted(payload.keys())
            data = payload.get("data")
            data_type = type(data).__name__
            data_summary: dict[str, str] = {}
            if isinstance(data, dict):
                for k, v in data.items():
                    if isinstance(v, list):
                        preview = str(v[:3])[:300]
                        data_summary[k] = f"list(len={len(v)}) {preview}"
                    elif isinstance(v, dict):
                        data_summary[k] = f"dict(keys={sorted(v.keys())[:12]})"
                    else:
                        data_summary[k] = f"{type(v).__name__}={str(v)[:60]}"
            logger.info(
                "list_reservations top=%s data_type=%s summary=%s",
                top_keys, data_type, data_summary,
            )
        results = map_my_reservations(payload)
        logger.info("list_reservations parsed %d reservations", len(results))
        return results

    def fetch_seat_map(
        self,
        studio_lesson_id: int,
        *,
        capacity_hint: int | None = None,
        studio_room_space_id: int | None = None,
    ) -> SeatMap:
        # 公開月間 API 由来の lesson は studio_lesson_id を持たない（= 0）。
        # この場合は予約済み席 API を叩けないので、taken は空で返し、配置のみ復元する。
        taken: list[int]
        if studio_lesson_id:
            response = self._client.list_reserved_nos(studio_lesson_id)
            taken = map_reserved_nos(response.payload)
        else:
            taken = []

        # 直近 fetch_week で構築した space_index から配置を引く。
        positions: list[SeatPosition] = []
        if studio_room_space_id is not None:
            positions = list(self._space_index.get(studio_room_space_id, ()))

        capacity = capacity_hint or len(positions) or (max(taken) if taken else 0)

        grid_cols = 0
        grid_rows = 0
        if positions:
            grid_cols = max(p.coord_x for p in positions) + 1
            grid_rows = max(p.coord_y for p in positions) + 1

        return SeatMap(
            studio_lesson_id=studio_lesson_id,
            capacity=capacity,
            taken_nos=sorted(set(taken)),
            positions=positions,
            grid_cols=grid_cols,
            grid_rows=grid_rows,
        )

    # --- 書き込み系（dry-run 時は擬似応答） ----------------

    def attempt_reservation(
        self,
        studio_lesson_id: int,
        no_preferences: list[int],
    ) -> ReservationAttempt:
        if not no_preferences:
            # 希望席が未指定の場合は「空席を自動で選択」する。
            # 方針:
            #   1) 予約済み席 API (list_reserved_nos) で現在埋まっている席を取得
            #   2) 1〜50 番のうち埋まっていない番号を昇順に preferences として試す
            #      （定員上限は studio_lesson_id からは取れないため、多めの 50 を
            #       試し、hacomono 側で無効席なら SeatUnavailable が返ってきて
            #       次の候補に進む想定）
            # list_reserved_nos が失敗しても致命的ではない: taken=空で 1〜50 を
            # 全部試すフォールバックで続行する。
            try:
                nos_resp = self._client.list_reserved_nos(studio_lesson_id)
                taken = set(map_reserved_nos(nos_resp.payload))
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "attempt_reservation fallback: list_reserved_nos failed "
                    "(studio_lesson_id=%d): %s",
                    studio_lesson_id, exc,
                )
                taken = set()
            candidate = [n for n in range(1, 51) if n not in taken]
            if not candidate:
                return ReservationAttempt(
                    ok=False,
                    studio_lesson_id=studio_lesson_id,
                    attempted_preferences=[],
                    message="空席が見つかりませんでした",
                    failure_reason="NoAvailableSeat",
                )
            no_preferences = candidate
            logger.info(
                "attempt_reservation: no preferences specified, auto-select "
                "candidates (studio_lesson_id=%d, taken=%d, try=%d)",
                studio_lesson_id, len(taken), len(candidate),
            )
        if self._dry_run:
            return self._attempt_dry_run(studio_lesson_id, no_preferences)
        return self._attempt_live(studio_lesson_id, no_preferences)

    def cancel_reservation(self, external_id: int) -> None:
        if self._dry_run:
            logger.info("dry-run: skip cancel external_id=%d", external_id)
            return
        try:
            self._client.cancel([external_id])
        except BusinessRuleViolation as exc:
            logger.warning("cancel rejected by server: %s", exc)
            raise

    def change_seat(self, external_id: int, new_no: int) -> None:
        if self._dry_run:
            logger.info("dry-run: skip move external_id=%d new_no=%d", external_id, new_no)
            return
        self._client.move(reservation_id=external_id, no=new_no)

    # --- 内部 --------------------------------------------

    def _attempt_dry_run(
        self,
        studio_lesson_id: int,
        no_preferences: list[int],
    ) -> ReservationAttempt:
        """dry-run: 第 1 希望を成功扱い、実際には POST しない。"""

        pick = no_preferences[0]
        fake_id = abs(hash(("dry", studio_lesson_id, pick))) % 10**9
        return ReservationAttempt(
            ok=True,
            studio_lesson_id=studio_lesson_id,
            attempted_preferences=list(no_preferences),
            seat_no=pick,
            external_id=fake_id,
            message="dry-run のため実際の予約 POST は送っていません",
        )

    def _attempt_live(
        self,
        studio_lesson_id: int,
        no_preferences: list[int],
    ) -> ReservationAttempt:
        last_reason = "SeatUnavailable"
        last_message = "希望席がすべて埋まっていました"
        for no in no_preferences:
            try:
                response = self._client.reserve(
                    studio_lesson_id=studio_lesson_id,
                    no=no,
                )
            except SeatUnavailable as exc:
                last_reason = "SeatUnavailable"
                last_message = str(exc) or "席が取れませんでした"
                continue
            except AlreadyReserved as exc:
                return ReservationAttempt(
                    ok=False,
                    studio_lesson_id=studio_lesson_id,
                    attempted_preferences=list(no_preferences),
                    message=str(exc) or "既に予約済みでした",
                    failure_reason="AlreadyReserved",
                )
            except CapacityExceeded as exc:
                return ReservationAttempt(
                    ok=False,
                    studio_lesson_id=studio_lesson_id,
                    attempted_preferences=list(no_preferences),
                    message=str(exc) or "レッスンが満席でした",
                    failure_reason="CapacityExceeded",
                )
            except OutsideReservationWindow as exc:
                return ReservationAttempt(
                    ok=False,
                    studio_lesson_id=studio_lesson_id,
                    attempted_preferences=list(no_preferences),
                    message=str(exc) or "予約可能な時間帯外でした",
                    failure_reason="OutsideReservationWindow",
                )
            except HacomonoError as exc:
                return ReservationAttempt(
                    ok=False,
                    studio_lesson_id=studio_lesson_id,
                    attempted_preferences=list(no_preferences),
                    message=str(exc),
                    failure_reason=type(exc).__name__,
                )
            return map_reservation_result(
                response.payload,
                studio_lesson_id=studio_lesson_id,
                attempted_preferences=no_preferences,
            )
        return ReservationAttempt(
            ok=False,
            studio_lesson_id=studio_lesson_id,
            attempted_preferences=list(no_preferences),
            message=last_message,
            failure_reason=last_reason,
        )
