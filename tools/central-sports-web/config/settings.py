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
    warm_up_hour: int = 8
    warm_up_minute: int = 55
    run_hour: int = 9
    run_minute: int = 0
    misfire_grace_time_seconds: int = 120

    # カレンダー表示（9:15 など分単位の枠も取りこぼさないよう 9 時から）
    calendar_start_hour: int = 9
    calendar_end_hour: int = 21

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
