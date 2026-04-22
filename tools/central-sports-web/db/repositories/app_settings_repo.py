"""app_settings テーブルの読み書き。

設定画面からの編集値を保存する override ストア。
key は pydantic Settings のフィールド名（snake_case）。value は文字列。
起動時に `app/services/app_settings_loader.py` で os.environ に注入され、
pydantic 経由で Settings を上書きする。
"""

from __future__ import annotations

from pathlib import Path

from db.connection import read_connection, write_transaction


def get_all(db_path: Path) -> dict[str, str]:
    """全キー → 値 の dict を返す。app_settings テーブルが無ければ空 dict。"""

    with read_connection(db_path) as con:
        try:
            rows = con.execute(
                "SELECT key, value FROM app_settings"
            ).fetchall()
        except Exception:
            # マイグレーション前の呼び出し等。呼び出し側が冪等に扱えるよう空返却。
            return {}
    return {str(row["key"]): str(row["value"]) for row in rows}


def upsert(db_path: Path, key: str, value: str) -> None:
    """1 件の設定を upsert する。updated_at は自動更新。"""

    with write_transaction(db_path) as con:
        con.execute(
            """
            INSERT INTO app_settings (key, value, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (str(key), str(value)),
        )


def delete(db_path: Path, key: str) -> None:
    """1 件の設定を削除する（指定 key が存在しなければ何もしない）。"""

    with write_transaction(db_path) as con:
        con.execute("DELETE FROM app_settings WHERE key = ?", (str(key),))


__all__ = ["get_all", "upsert", "delete"]
