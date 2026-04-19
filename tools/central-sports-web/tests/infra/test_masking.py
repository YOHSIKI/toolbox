from __future__ import annotations

from infra.hacomono.masking import (
    clear_secret_values,
    mask_secrets,
    register_secret_values,
)


def setup_function(_: object) -> None:
    clear_secret_values()


def test_register_and_mask_replaces_value() -> None:
    register_secret_values("supersecret1234")
    assert mask_secrets("prefix supersecret1234 suffix") == "prefix *** suffix"


def test_multiple_values_masked() -> None:
    register_secret_values("alice@example.com", "p4ssw0rd")
    masked = mask_secrets("user=alice@example.com pass=p4ssw0rd")
    assert "alice@example.com" not in masked
    assert "p4ssw0rd" not in masked
    assert masked.count("***") == 2


def test_short_value_not_registered() -> None:
    register_secret_values("abc")
    assert mask_secrets("abc in text") == "abc in text"


def test_longer_value_masked_before_shorter_overlap() -> None:
    register_secret_values("token", "token123456")
    # 長い値から置換されるため、token123456 → ***、その後 "token" は無害
    assert mask_secrets("token123456 then token") == "*** then ***"


def test_none_values_ignored() -> None:
    register_secret_values(None, "validvalue")
    assert mask_secrets("validvalue check") == "*** check"
