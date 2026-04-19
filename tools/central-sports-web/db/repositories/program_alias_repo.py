"""公開月間 API の progcd → reserve API 表記のエイリアス学習リポジトリ。

- 公開月間 API は `progcd` ("A0450", "AA756" 等) と揺れた program_name を返す
- reserve API は「真の表示名」と program_id を返すが、窓は 7 日前後
- 両方で観測できた枠の `progcd` → (reserve の program_id, program_name) を永続化し、
  reserve 窓の外の公開月間 lesson に対しても同じ正規表記で表示できるようにする

進め方: 毎回 fetch_monthly_public で observed と merge した結果を使って alias を
積み上げる。progcd が揃っていれば週替わりサブプログラムでも安全に引ける。
"""

from __future__ import annotations

from pathlib import Path

from db.connection import read_connection, write_transaction


def upsert_alias(
    db_path: Path,
    *,
    studio_id: int,
    studio_room_id: int,
    progcd: str,
    program_id: str | None,
    program_name: str,
) -> None:
    """progcd → (program_id, program_name) の alias を 1 件 upsert する。

    program_id が None のときは既存値を温存する (COALESCE)。
    program_name は最新の観測値で上書きする。
    """

    with write_transaction(db_path) as con:
        con.execute(
            """
            INSERT INTO program_aliases
                (studio_id, studio_room_id, progcd, program_id, program_name, observed_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(studio_id, studio_room_id, progcd) DO UPDATE SET
                program_id = COALESCE(excluded.program_id, program_aliases.program_id),
                program_name = excluded.program_name,
                observed_at = excluded.observed_at
            """,
            (
                int(studio_id),
                int(studio_room_id),
                str(progcd),
                None if program_id is None else str(program_id),
                str(program_name),
            ),
        )


def list_aliases(
    db_path: Path,
    *,
    studio_id: int,
    studio_room_id: int,
) -> dict[str, dict]:
    """progcd → {program_id, program_name, observed_at} の dict を返す。

    起動時のメモリ復元と、fetch_monthly_public の lazy load の両方から呼ばれる。
    """

    with read_connection(db_path) as con:
        rows = con.execute(
            """
            SELECT progcd, program_id, program_name, observed_at
            FROM program_aliases
            WHERE studio_id = ? AND studio_room_id = ?
            """,
            (int(studio_id), int(studio_room_id)),
        ).fetchall()
    return {
        str(r["progcd"]): {
            "program_id": r["program_id"],
            "program_name": r["program_name"],
            "observed_at": r["observed_at"],
        }
        for r in rows
    }


__all__ = ["upsert_alias", "list_aliases"]
