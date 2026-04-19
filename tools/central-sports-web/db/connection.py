"""SQLite 接続管理。

- 読み取りは並列可、書き込みはプロセス内で 1 本化（`_write_lock`）
- WAL + NORMAL で、書き込みスループットと耐久性のバランスを取る
- 呼び出しごとにコネクションを開き close する（接続プールは持たない、SQLite の想定用法）
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_write_lock = threading.Lock()


def _configure(connection: sqlite3.Connection) -> None:
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")


def open_connection(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(
        str(db_path), isolation_level=None, check_same_thread=False
    )
    _configure(connection)
    return connection


@contextmanager
def read_connection(db_path: Path) -> Iterator[sqlite3.Connection]:
    """読み取り用コネクションを払い出す。"""

    connection = open_connection(db_path)
    try:
        yield connection
    finally:
        connection.close()


@contextmanager
def write_transaction(db_path: Path) -> Iterator[sqlite3.Connection]:
    """書き込み専用の排他トランザクション。プロセス内で直列化される。"""

    with _write_lock:
        connection = open_connection(db_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()
