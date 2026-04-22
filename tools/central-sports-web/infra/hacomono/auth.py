"""ログインセッションと失効時の再試行管理。

- device_id は起動時に永続ファイルからロード、無ければ生成して保存
- email / password はメモリのみで保持（平文を永続ストアに書かない）
- SessionExpired を検知したら 1 回だけ再ログイン、3 連敗で LockedOut
- 書き込み系の再ログインと呼び出しは RLock でシリアライズ
"""

from __future__ import annotations

import logging
import secrets
import threading
from dataclasses import dataclass, field
from pathlib import Path

from infra.hacomono.endpoints import SIGNIN_PATH, SIGNOUT_PATH, login_referer
from infra.hacomono.errors import (
    AuthenticationError,
    HacomonoError,
    InvalidCredentials,
    LockedOut,
)
from infra.hacomono.http import HttpBackend, RawResponse
from infra.hacomono.masking import register_secret_values

logger = logging.getLogger(__name__)

# 連続認証失敗のロックアウト上限のデフォルト。
# 実運用値は `config.settings.Settings.max_consecutive_failures` で差し替える。
DEFAULT_MAX_CONSECUTIVE_FAILURES = 3
DEVICE_ID_LEN = 40
_HEX_CHARS = set("0123456789abcdef")


def generate_device_id() -> str:
    """ブラウザ相当のランダム 40 文字 hex を生成する。"""

    return secrets.token_hex(20)


def load_or_create_device_id(path: Path) -> str:
    """device_id を永続ファイルからロード。不正値や不在なら生成して保存し直す。"""

    if path.exists():
        existing = path.read_text(encoding="utf-8").strip().lower()
        if len(existing) == DEVICE_ID_LEN and all(c in _HEX_CHARS for c in existing):
            return existing
        logger.warning("device_id file invalid, regenerating at %s", path)
    path.parent.mkdir(parents=True, exist_ok=True)
    new_id = generate_device_id()
    path.write_text(new_id, encoding="utf-8")
    try:
        path.chmod(0o600)
    except Exception:  # pragma: no cover - 権限設定できない FS では無視
        pass
    return new_id


# --- セッション失効の検知 --------------------------------------------

_EXPIRED_CODES = {
    "E_UNAUTHORIZED",
    "E_AUTH_REQUIRED",
    "E_SESSION_EXPIRED",
    "E_NEED_LOGIN",
}


def is_session_expired_response(response: RawResponse) -> bool:
    """401 または認証失効を示す code を含むかどうか。"""

    if response.status_code == 401:
        return True
    errors = response.payload.get("errors") if isinstance(response.payload, dict) else None
    if not isinstance(errors, list):
        return False
    for err in errors:
        if not isinstance(err, dict):
            continue
        code = str(err.get("code") or "")
        if code in _EXPIRED_CODES:
            return True
    return False


# --- AuthSession -----------------------------------------------------

@dataclass
class AuthSession:
    """HTTP セッションとログイン状態を束ねる。

    メンバーの email/password は読み書きとも内部からのみ。外に渡してはならない。
    """

    email: str
    password: str
    http: HttpBackend
    max_consecutive_failures: int = DEFAULT_MAX_CONSECUTIVE_FAILURES
    lock: threading.RLock = field(default_factory=threading.RLock)
    _consecutive_failures: int = 0
    _locked_out: bool = False
    _locked_out_notified: bool = False

    def __post_init__(self) -> None:
        # 秘匿値をマスキング対象に登録（ログや例外メッセージで漏れないよう）
        register_secret_values(self.email, self.password, self.http.device_id)

    @property
    def is_authenticated(self) -> bool:
        return self.http.access_token is not None

    @property
    def is_locked_out(self) -> bool:
        return self._locked_out

    def consume_locked_out_notification(self) -> bool:
        """ロックアウト通知をまだ送っていなければ True を返し、次回以降は False。"""

        with self.lock:
            if self._locked_out and not self._locked_out_notified:
                self._locked_out_notified = True
                return True
            return False

    # --- ログイン系 ---

    def sign_in(self) -> None:
        """ログインして `_at` Cookie を取得する。"""

        with self.lock:
            if self._locked_out:
                raise LockedOut("authentication is locked out")
            response = self.http.post_json(
                SIGNIN_PATH,
                {"mail_address": self.email, "password": self.password},
                referer=login_referer(),
            )
            self._handle_signin_response(response)

    def _handle_signin_response(self, response: RawResponse) -> None:
        errors = response.payload.get("errors") if isinstance(response.payload, dict) else None
        status = response.status_code
        has_errors = isinstance(errors, list) and len(errors) > 0

        if has_errors or status >= 400:
            self._consecutive_failures += 1
            codes: list[str] = [
                str(e.get("code"))
                for e in (errors or [])
                if isinstance(e, dict) and e.get("code")
            ]
            if self._consecutive_failures >= self.max_consecutive_failures:
                self._locked_out = True
                raise LockedOut(
                    f"locked out after {self._consecutive_failures} failed sign-ins"
                )
            if codes and any(
                c in {"E_AUTH_INVALID", "E_INVALID_CREDENTIALS", "E_LOGIN_FAILED"}
                for c in codes
            ):
                raise InvalidCredentials(
                    f"signin failed (codes={codes})",
                    code=codes[0],
                    raw={"errors": errors},
                )
            raise AuthenticationError(
                f"signin failed (codes={codes or ['<unknown>']})",
                code=codes[0] if codes else None,
                raw={"errors": errors, "status": status},
            )

        if self.http.access_token is None:
            raise AuthenticationError("signin succeeded but _at cookie was not set")
        # トークンをマスキング対象に追加登録（シグイン後に取得できるため）
        register_secret_values(self.http.access_token)
        self._consecutive_failures = 0
        logger.info("signin succeeded")

    def sign_out(self) -> None:
        """可能であればサーバ側にログアウトを通知する。失敗は握りつぶす。"""

        if not self.is_authenticated:
            return
        with self.lock:
            try:
                self.http.post_json(SIGNOUT_PATH, {}, referer=login_referer())
            except HacomonoError as exc:
                logger.info("signout ignored: %s", exc)

    def reauthenticate_once(self) -> None:
        """失効検知時に 1 回だけ試みる再ログイン。"""

        with self.lock:
            if self._locked_out:
                raise LockedOut("authentication is locked out")
            logger.warning("re-authenticating due to session expiration")
            self.sign_in()
