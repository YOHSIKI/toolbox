"""設定画面（Phase 2: 編集可）の view 組み立て。

Phase 2 では一部項目を編集可にした。`SettingItem.editable=True` の項目は
テンプレート側で input に展開され、`/reserve/settings/update` に POST される。
編集不可項目は従来通り read-only 表示。

秘匿項目は取得も参照もしない（UI に一切出さない）:
- ``basic_auth_user`` / ``basic_auth_password`` / ``basic_auth_enabled``
- ``discord_webhook_url``
- ``secrets_dir`` / ``secrets_group``

出典メタ情報（ファイル名・行番号）はハードコードで保持する。
自動追跡はしていないため、参照先コードの行番号を動かした際は
本ファイル側も併せて更新すること。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from config.settings import Settings


@dataclass(frozen=True)
class SettingSource:
    """値の出典（ファイル名 + 行番号）。"""

    file: str
    line: int


@dataclass(frozen=True)
class SettingItem:
    """1 行分の設定表示。

    編集可項目は `editable=True` にし、`edit_key` に app_settings 上の
    保存キー（settings のフィールド名または時刻用の合成キー）を入れる。
    `current_raw` は input の value 属性に入れる生値（HH:MM / 数値 / "true"）。
    """

    label: str
    param_name: str
    value_display: str
    note: str | None
    source: SettingSource
    editable: bool = False
    edit_key: str | None = None
    edit_type: str = "text"  # "text" | "number" | "time" | "toggle" | "select"
    edit_min: float | None = None
    edit_max: float | None = None
    edit_step: float | None = None
    edit_options: list[str] | None = field(default_factory=lambda: None)
    current_raw: str | None = None


@dataclass(frozen=True)
class SettingSection:
    """セクション（1 ページに並べる）。"""

    title: str
    description: str | None
    items: list[SettingItem]


def _fmt_time(hour: int, minute: int) -> str:
    return f"{hour:02d}:{minute:02d}"


def _build_scheduler_section(settings: Settings) -> SettingSection:
    warmup_time = _fmt_time(
        settings.login_warmup_hour, settings.login_warmup_minute
    )
    run_time = _fmt_time(settings.auto_booking_hour, settings.auto_booking_minute)
    refresh_time = _fmt_time(
        settings.schedule_refresh_hour, settings.schedule_refresh_minute
    )
    sync_time = _fmt_time(
        settings.my_reservations_sync_hour, settings.my_reservations_sync_minute
    )
    items = [
        SettingItem(
            label="スケジューラ有効",
            param_name="scheduler_enabled",
            value_display=str(settings.scheduler_enabled).lower(),
            note=(
                "定期ジョブ（予約実行・同期など）の稼働可否。"
                "無効のとき、以下すべての時刻設定を停止する。"
            ),
            source=SettingSource("config/settings.py", 60),
            editable=True,
            edit_key="scheduler_enabled",
            edit_type="toggle",
            current_raw="true" if settings.scheduler_enabled else "false",
        ),
        SettingItem(
            label="事前ログインの時刻",
            param_name="login_warmup_hour / login_warmup_minute",
            value_display=f"毎日 {warmup_time}",
            note="自動予約の直前にセッションを確立する時刻。",
            source=SettingSource("scheduler/runtime.py", 37),
            editable=True,
            edit_key="login_warmup_time",
            edit_type="time",
            current_raw=warmup_time,
        ),
        SettingItem(
            label="自動予約の実行時刻",
            param_name="auto_booking_hour / auto_booking_minute",
            value_display=f"毎日 {run_time}",
            note="定期予約および予約予定（intent）を実行する時刻。",
            source=SettingSource("scheduler/runtime.py", 50),
            editable=True,
            edit_key="auto_booking_time",
            edit_type="time",
            current_raw=run_time,
        ),
        SettingItem(
            label="時間割の取得時刻",
            param_name="schedule_refresh_hour / schedule_refresh_minute",
            value_display=f"毎日 {refresh_time}",
            note="時間割キャッシュおよびプログラム名の対応表を更新する時刻。",
            source=SettingSource("scheduler/runtime.py", 68),
            editable=True,
            edit_key="schedule_refresh_time",
            edit_type="time",
            current_raw=refresh_time,
        ),
        SettingItem(
            label="予約一覧の同期時刻",
            param_name="my_reservations_sync_hour / my_reservations_sync_minute",
            value_display=f"毎日 {sync_time}",
            note="外部予約一覧を取得し、ローカルの予約状態と同期する時刻。",
            source=SettingSource("scheduler/runtime.py", 80),
            editable=True,
            edit_key="my_reservations_sync_time",
            edit_type="time",
            current_raw=sync_time,
        ),
    ]
    return SettingSection(
        title="スケジューラ",
        description="定期ジョブの稼働設定および実行時刻。",
        items=items,
    )


def _build_display_section(settings: Settings) -> SettingSection:
    return SettingSection(
        title="表示",
        description="画面の描画範囲および表示件数。",
        items=[
            SettingItem(
                label="カレンダー開始時刻",
                param_name="calendar_start_time",
                value_display=f"{settings.calendar_start_time} 時",
                note="時間割カレンダーの描画開始時刻（時刻軸の上端）。",
                source=SettingSource("config/settings.py", 77),
                editable=True,
                edit_key="calendar_start_time",
                edit_type="number",
                edit_min=0,
                edit_max=23,
                edit_step=1,
                current_raw=str(settings.calendar_start_time),
            ),
            SettingItem(
                label="カレンダー終了時刻",
                param_name="calendar_end_time",
                value_display=f"{settings.calendar_end_time} 時",
                note="時間割カレンダーの描画終了時刻（時刻軸の下端）。",
                source=SettingSource("config/settings.py", 78),
                editable=True,
                edit_key="calendar_end_time",
                edit_type="number",
                edit_min=0,
                edit_max=23,
                edit_step=1,
                current_raw=str(settings.calendar_end_time),
            ),
            SettingItem(
                label="履歴表示件数",
                param_name="history_display_limit",
                value_display=f"{settings.history_display_limit} 件",
                note=(
                    "予約履歴カードに表示する最大件数。"
                    "超過分は画面に表示しない（データベースには保持する）。"
                ),
                source=SettingSource("config/settings.py", 81),
                editable=True,
                edit_key="history_display_limit",
                edit_type="number",
                edit_min=1,
                edit_max=200,
                edit_step=1,
                current_raw=str(settings.history_display_limit),
            ),
        ],
    )


def _build_similarity_section(settings: Settings) -> SettingSection:
    return SettingSection(
        title="プログラム名の一致判定",
        description="公開月間 API と予約 API のプログラム名の同一性を判定するための類似度しきい値。",
        items=[
            SettingItem(
                label="同一プログラムとみなす類似度の下限",
                param_name="alias_sim_accept",
                value_display=f"{settings.alias_sim_accept:.2f}",
                note=(
                    "同一プログラムの表記揺れと判定する類似度の下限値。"
                    "本値以上のとき、対応表（alias）を更新する。"
                ),
                source=SettingSource("config/settings.py", 87),
                editable=True,
                edit_key="alias_sim_accept",
                edit_type="number",
                edit_min=0,
                edit_max=1,
                edit_step=0.01,
                current_raw=f"{settings.alias_sim_accept:.2f}",
            ),
            SettingItem(
                label="別プログラムとみなす類似度の上限",
                param_name="alias_sim_warn",
                value_display=f"{settings.alias_sim_warn:.2f}",
                note=(
                    "別プログラムと判定する類似度の上限値。"
                    "本値未満のとき、対応表の更新をスキップする。"
                    "本値以上かつ下限値未満の範囲では、警告を記録した上で更新する。"
                ),
                source=SettingSource("config/settings.py", 88),
                editable=True,
                edit_key="alias_sim_warn",
                edit_type="number",
                edit_min=0,
                edit_max=1,
                edit_step=0.01,
                current_raw=f"{settings.alias_sim_warn:.2f}",
            ),
        ],
    )


def _build_timeout_section(settings: Settings) -> SettingSection:
    return SettingSection(
        title="接続・タイムアウト",
        description="外部 API 呼び出しの応答待ち制限。",
        items=[
            SettingItem(
                label="予約 API のタイムアウト",
                param_name="reserve_timeout_seconds",
                value_display=f"{settings.reserve_timeout_seconds:g} 秒",
                note="予約システム API（認証・予約・座席取得）の応答待ち上限。",
                source=SettingSource("config/settings.py", 91),
                editable=True,
                edit_key="reserve_timeout_seconds",
                edit_type="number",
                edit_min=1,
                edit_max=60,
                edit_step=0.5,
                current_raw=f"{settings.reserve_timeout_seconds:g}",
            ),
            SettingItem(
                label="公開月間 API のタイムアウト",
                param_name="public_monthly_timeout_seconds",
                value_display=f"{settings.public_monthly_timeout_seconds:g} 秒",
                note="公開月間スケジュール API の応答待ち上限。",
                source=SettingSource("config/settings.py", 92),
                editable=True,
                edit_key="public_monthly_timeout_seconds",
                edit_type="number",
                edit_min=1,
                edit_max=60,
                edit_step=0.5,
                current_raw=f"{settings.public_monthly_timeout_seconds:g}",
            ),
            SettingItem(
                label="連続認証失敗の上限回数",
                param_name="max_consecutive_failures",
                value_display=f"{settings.max_consecutive_failures} 回",
                note=(
                    "連続認証失敗の許容回数。"
                    "上限を超過した場合は外部通信を停止し、通知を送信する。"
                    "再開には手動リセットを要する。"
                ),
                source=SettingSource("config/settings.py", 95),
                editable=True,
                edit_key="max_consecutive_failures",
                edit_type="number",
                edit_min=1,
                edit_max=10,
                edit_step=1,
                current_raw=str(settings.max_consecutive_failures),
            ),
        ],
    )


def _build_studio_section(settings: Settings) -> SettingSection:
    return SettingSection(
        title="店舗",
        description="初期表示のデフォルト店舗。店舗の切替はトップバーから行う。",
        items=[
            SettingItem(
                label="デフォルト店舗 ID",
                param_name="default_studio_id",
                value_display=str(settings.default_studio_id),
                note="起動時および未選択時に表示する店舗の内部 ID。",
                source=SettingSource("config/settings.py", 52),
            ),
            SettingItem(
                label="デフォルトのスタジオルーム ID",
                param_name="default_studio_room_id",
                value_display=str(settings.default_studio_room_id),
                note="起動時および未選択時に表示するスタジオルームの内部 ID。",
                source=SettingSource("config/settings.py", 53),
            ),
            SettingItem(
                label="デフォルト店舗名",
                param_name="default_studio_name",
                value_display=settings.default_studio_name,
                note="画面右上に表示する店舗名。",
                source=SettingSource("config/settings.py", 54),
            ),
        ],
    )


def _build_retention_section(settings: Settings) -> SettingSection:
    return SettingSection(
        title="保持期間",
        description="履歴データを保持する期間。",
        items=[
            SettingItem(
                label="実行履歴の保持日数",
                param_name="history_keep_days",
                value_display=f"{settings.history_keep_days} 日",
                note=(
                    "実行履歴を保持する日数。"
                    "超過分は毎日 0 時の定期処理で削除する。"
                    "時間割・予約・定期予約・学習結果は削除対象外。"
                ),
                source=SettingSource("config/settings.py", 98),
                editable=True,
                edit_key="history_keep_days",
                edit_type="number",
                edit_min=1,
                edit_max=365,
                edit_step=1,
                current_raw=str(settings.history_keep_days),
            ),
        ],
    )


def build_settings_view(settings: Settings) -> list[SettingSection]:
    """設定画面に表示する 6 セクションを組み立てて返す。

    秘匿情報（basic_auth / discord_webhook / secrets_dir / secrets_group）は
    ここから取得も参照もしない。
    アプリ名 / バージョン等のメタ情報はサイドバーのフッターで表示するため、
    この画面には含めない。
    """

    return [
        _build_scheduler_section(settings),
        _build_display_section(settings),
        _build_similarity_section(settings),
        _build_timeout_section(settings),
        _build_studio_section(settings),
        _build_retention_section(settings),
    ]


__all__ = [
    "SettingSource",
    "SettingItem",
    "SettingSection",
    "build_settings_view",
]
