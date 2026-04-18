"""Central Sports 用の最小 SecretManager（Fernet 復号のみ）

dev-admin からは `/workspace/.secrets/` が見えないため、本スクリプトは
toolbox-exec コンテナでの実行を前提とする。`gateway exec` 経由で起動される。
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from cryptography.fernet import Fernet

DEFAULT_SECRETS_DIR = "/workspace/.secrets"


def get_group(identifier: str, secrets_dir: str | None = None) -> dict[str, str]:
    """`.secrets.yaml.enc` を復号し、指定 identifier の group を返す。

    戻り値は平文の dict（email/password 等）。呼び出し側でログ出力や
    stdout 書き出しをしないよう注意する。
    """
    sdir = secrets_dir or os.environ.get("SECRETS_DIR", DEFAULT_SECRETS_DIR)
    master_key_path = Path(sdir) / ".master.key"
    encrypted_path = Path(sdir) / ".secrets.yaml.enc"

    if not master_key_path.exists():
        raise RuntimeError(f"Master key not found: {master_key_path}")
    if not encrypted_path.exists():
        raise RuntimeError(f"Encrypted secrets not found: {encrypted_path}")

    key = master_key_path.read_bytes().strip()
    fernet = Fernet(key)
    decrypted = fernet.decrypt(encrypted_path.read_bytes())
    data: dict = yaml.safe_load(decrypted.decode("utf-8")) or {}
    group = data.get(identifier)
    if not isinstance(group, dict):
        raise RuntimeError(f"Group '{identifier}' not found in secrets")
    return {str(k): str(v) for k, v in group.items()}
