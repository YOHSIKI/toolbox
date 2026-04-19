from __future__ import annotations

import re
from pathlib import Path

from infra.hacomono.auth import (
    DEVICE_ID_LEN,
    generate_device_id,
    load_or_create_device_id,
)


def test_generate_device_id_is_40_hex() -> None:
    did = generate_device_id()
    assert len(did) == DEVICE_ID_LEN
    assert re.fullmatch(r"[0-9a-f]{40}", did)


def test_load_or_create_when_missing(tmp_path: Path) -> None:
    path = tmp_path / "device_id.txt"
    did = load_or_create_device_id(path)
    assert len(did) == DEVICE_ID_LEN
    assert path.read_text(encoding="utf-8").strip() == did


def test_load_or_create_reuses_existing(tmp_path: Path) -> None:
    path = tmp_path / "device_id.txt"
    path.write_text("0123456789abcdef0123456789abcdef01234567", encoding="utf-8")
    did = load_or_create_device_id(path)
    assert did == "0123456789abcdef0123456789abcdef01234567"


def test_load_or_create_regenerates_invalid(tmp_path: Path) -> None:
    path = tmp_path / "device_id.txt"
    path.write_text("not-a-hex", encoding="utf-8")
    did = load_or_create_device_id(path)
    assert did != "not-a-hex"
    assert re.fullmatch(r"[0-9a-f]{40}", did)
