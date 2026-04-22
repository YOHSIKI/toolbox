"""アプリ全体の実行時設定（pydantic-settings ベース）。

- `.env` と環境変数から読み取る
- 値は不変の Settings インスタンスに集約
- `get_settings()` でキャッシュ済みインスタンスを取得
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CSW_",
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "central-sports-web"
    version: str = "0.1.0"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 8080
    timezone: str = "Asia/Tokyo"

    # 書き込み系 API（予約 POST / キャンセル / 席変更）を抑止するかどうか
    dry_run: bool = True

    # 永続ストア / ファイル
    data_dir: Path = BASE_DIR / "data"
    database_path: Path | None = None
    device_id_path: Path | None = None

    # Basic 認証
    basic_auth_user: str = "admin"
    basic_auth_password: str = "changeme"
    basic_auth_enabled: bool = True

    # シークレット
    secrets_dir: Path = Path("/workspace/.secrets")
    secrets_group: str = "central-sports"

    # 既定店舗（初期シードと一致）
    default_studio_id: int = 79
    default_studio_room_id: int = 177
    default_studio_name: str = "セントラルフィットネスクラブ府中"

    # Discord Webhook
    discord_webhook_url: str | None = None

    # スケジューラ
    scheduler_enabled: bool = True
    login_warmup_hour: int = 8
    login_warmup_minute: int = 55
    auto_booking_hour: int = 9
    auto_booking_minute: int = 0
    # 旧: misfire_grace_time_seconds（削除済み。scheduler 側で固定 60 秒）

    # スケジューラ（以前ハードコードしていた cron を設定化）
    schedule_refresh_hour: int = 0
    schedule_refresh_minute: int = 5
    my_reservations_sync_hour: int = 0
    my_reservations_sync_minute: int = 0
    # 履歴クリーンアップは毎日 0 時に固定（保持日数で期限判定するため、
    # 曜日や時刻をパラメータ化する意味がない）

    # カレンダー表示（9:15 など分単位の枠も取りこぼさないよう 9 時から）
    calendar_start_time: int = 9
    calendar_end_time: int = 21

    # 履歴表示（ダッシュボードの「予約履歴」カードの最大件数）
    history_display_limit: int = 30

    # プログラム名の一致判定（hacomono_gateway の alias 自動学習しきい値）
    #   median >= alias_sim_accept           → 通常 upsert
    #   alias_sim_warn ≤ median < accept     → warning 付き upsert
    #   median < alias_sim_warn              → skip（誤学習防止）
    # 値は検証ペア 16 件で clean cut が成立する点に合わせる:
    #   同一群 min=0.636 / 別物群 max=0.571 / margin=+0.065
    alias_sim_accept: float = 0.61
    alias_sim_warn: float = 0.576

    # 外部 API タイムアウト（秒）
    reserve_timeout_seconds: float = 15.0
    public_monthly_timeout_seconds: float = 10.0

    # 連続認証失敗のロックアウト上限
    max_consecutive_failures: int = 3

    # 履歴保持日数（週次 retention ジョブで削除対象）
    history_keep_days: int = 90

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def db_file(self) -> Path:
        return self.database_path or (self.data_dir / "app.db")

    @property
    def device_id_file(self) -> Path:
        return self.device_id_path or (self.data_dir / "device_id.txt")


@lru_cache
def get_settings() -> Settings:
    return Settings()
