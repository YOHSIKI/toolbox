"""DI とアプリコンテキストの束ね。

lifespan で `AppContext` を 1 つ組み立て、`app.state.context` に格納する。
各ルートは `Depends(get_context)` で取り出す。
secrets が見つからない環境（dev-admin など）では gateway 系は None のまま
UI を起動する。UI は「未認証バナー」を出して、書き込み操作をブロックする。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from fastapi import Request

from app.services.booking_intent import BookingIntentService
from app.services.calendar_query import CalendarQueryService
from app.services.dashboard_query import DashboardQueryService
from app.services.reserve_recurring import RecurringService
from app.services.reserve_single import ReserveSingleService
from app.services.session_warmup import SessionWarmupService
from app.services.sync_my_reservations import SyncMyReservationsService
from config.settings import Settings
from infra.hacomono.auth import AuthSession, load_or_create_device_id
from infra.hacomono.client import HacomonoClient
from infra.hacomono.http import HttpBackend
from infra.hacomono.masking import install_log_filter, register_secret_values
from infra.hacomono.public_monthly import PublicMonthlyClient
from infra.notifier.discord import DiscordNotifier
from infra.secrets.fernet_store import try_load_group

logger = logging.getLogger(__name__)


@dataclass
class AppContext:
    settings: Settings
    db_path: Path
    device_id: str
    notifier: DiscordNotifier
    dashboard: DashboardQueryService
    # 以下は secrets がないと None
    http: HttpBackend | None
    auth: AuthSession | None
    client: HacomonoClient | None
    gateway: object | None  # ReservationGateway Protocol 実装
    calendar: CalendarQueryService | None
    reserve_single: ReserveSingleService | None
    recurring: RecurringService | None
    warmup: SessionWarmupService | None
    sync_reservations: SyncMyReservationsService | None
    booking_intent: BookingIntentService | None = None

    @property
    def is_fully_configured(self) -> bool:
        return self.gateway is not None


def build_context(settings: Settings) -> AppContext:
    """lifespan から呼ばれる 1 回きりの初期化。"""

    install_log_filter()
    notifier = DiscordNotifier(settings.discord_webhook_url)

    device_id = load_or_create_device_id(settings.device_id_file)
    register_secret_values(device_id)
    logger.info("device_id loaded (len=%d, masked)", len(device_id))

    bundle = try_load_group(settings.secrets_dir, settings.secrets_group)
    http: HttpBackend | None = None
    auth: AuthSession | None = None
    client: HacomonoClient | None = None
    gateway = None
    calendar_service: CalendarQueryService | None = None
    reserve_single: ReserveSingleService | None = None
    recurring: RecurringService | None = None
    warmup: SessionWarmupService | None = None
    sync_reservations: SyncMyReservationsService | None = None
    booking_intent: BookingIntentService | None = None

    if bundle is not None:
        email = bundle.values.get("email") or bundle.values.get("mail_address")
        password = bundle.values.get("password")
        if not email or not password:
            logger.warning(
                "secrets group '%s' missing email/password keys", settings.secrets_group
            )
        else:
            register_secret_values(email, password)
            http = HttpBackend(
                device_id=device_id,
                timeout=settings.reserve_timeout_seconds,
            )
            auth = AuthSession(
                email=email,
                password=password,
                http=http,
                max_consecutive_failures=settings.max_consecutive_failures,
            )
            client = HacomonoClient(http=http, auth=auth)
            public_client = PublicMonthlyClient(
                timeout=settings.public_monthly_timeout_seconds,
            )

            # ローカル import で循環を回避
            from app.adapters.hacomono_gateway import HacomonoGateway

            gateway = HacomonoGateway(
                client=client,
                auth=auth,
                dry_run=settings.dry_run,
                public_client=public_client,
                db_path=settings.db_file,
                alias_sim_accept=settings.alias_sim_accept,
                alias_sim_warn=settings.alias_sim_warn,
            )
            calendar_service = CalendarQueryService(
                settings.db_file, gateway, settings=settings
            )
            reserve_single = ReserveSingleService(settings.db_file, gateway)
            recurring = RecurringService(settings.db_file, gateway, settings)
            warmup = SessionWarmupService(settings.db_file, gateway, notifier)
            sync_reservations = SyncMyReservationsService(settings.db_file, gateway)
            booking_intent = BookingIntentService(settings.db_file, gateway, settings)
    else:
        logger.warning(
            "running without secrets: hacomono API is not reachable; UI only mode"
        )

    dashboard = DashboardQueryService(
        settings.db_file, settings, recurring_service=recurring
    )

    return AppContext(
        settings=settings,
        db_path=settings.db_file,
        device_id=device_id,
        notifier=notifier,
        dashboard=dashboard,
        http=http,
        auth=auth,
        client=client,
        gateway=gateway,
        calendar=calendar_service,
        reserve_single=reserve_single,
        recurring=recurring,
        warmup=warmup,
        sync_reservations=sync_reservations,
        booking_intent=booking_intent,
    )


def close_context(ctx: AppContext) -> None:
    """shutdown 時のクリーンアップ。"""

    if ctx.auth is not None:
        try:
            ctx.auth.sign_out()
        except Exception:  # noqa: BLE001
            pass
    if ctx.http is not None:
        ctx.http.close()


# --- FastAPI 依存注入 ----------------------------------------------

def get_context(request: Request) -> AppContext:
    ctx = getattr(request.app.state, "context", None)
    if ctx is None:  # pragma: no cover - lifespan が走る前は起きない想定
        raise RuntimeError("AppContext not initialised")
    return ctx
