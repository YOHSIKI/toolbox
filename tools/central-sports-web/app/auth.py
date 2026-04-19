"""Basic 認証の依存注入。

`auto_error=False` にして、無効化設定時は credentials 不在でも通す。
"""

from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from config.settings import Settings, get_settings

_security = HTTPBasic(realm="central-sports-web", auto_error=False)


def require_basic_auth(
    credentials: HTTPBasicCredentials | None = Depends(_security),
    settings: Settings = Depends(get_settings),
) -> str:
    if not settings.basic_auth_enabled:
        return "anonymous"
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="認証が必要です",
            headers={"WWW-Authenticate": 'Basic realm="central-sports-web"'},
        )
    user_ok = secrets.compare_digest(
        credentials.username.encode("utf-8"),
        settings.basic_auth_user.encode("utf-8"),
    )
    pass_ok = secrets.compare_digest(
        credentials.password.encode("utf-8"),
        settings.basic_auth_password.encode("utf-8"),
    )
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="認証に失敗しました",
            headers={"WWW-Authenticate": 'Basic realm="central-sports-web"'},
        )
    return credentials.username
