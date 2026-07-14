import pytest
from pydantic import ValidationError

from lookout.config import Settings


def test_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.interval_seconds == 300
    assert settings.cleanup is False


def test_env_prefix(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("LOOKOUT_INTERVAL_SECONDS", "60")
    settings = Settings(_env_file=None)
    assert settings.interval_seconds == 60


def test_list_fields_are_comma_separated_not_json(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("LOOKOUT_INCLUDE_NAMES", "foo,bar")
    monkeypatch.setenv("LOOKOUT_EXCLUDE_NAMES", "baz")
    monkeypatch.setenv("LOOKOUT_NOTIFICATION_URLS", "tgram://token/chat, mailto://user@example.com")
    settings = Settings(_env_file=None)
    assert settings.include_names == ["foo", "bar"]
    assert settings.exclude_names == ["baz"]
    assert settings.notification_urls == ["tgram://token/chat", "mailto://user@example.com"]


def test_unset_list_fields_default_to_empty(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    settings = Settings(_env_file=None)
    assert settings.include_names == []
    assert settings.exclude_names == []
    assert settings.notification_urls == []


def test_non_positive_interval_seconds_rejected() -> None:
    # interval_seconds <= 0 would turn run_forever's sleep loop into a hot
    # loop with no sleep at all.
    with pytest.raises(ValidationError):
        Settings(_env_file=None, interval_seconds=0)


def test_non_positive_interval_seconds_rejected_on_cli_override() -> None:
    settings = Settings(_env_file=None)
    with pytest.raises(ValidationError):
        settings.interval_seconds = -5


def test_negative_stop_timeout_seconds_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, stop_timeout_seconds=-1)


def test_invalid_log_level_rejected_with_friendly_message() -> None:
    with pytest.raises(ValidationError, match="invalid log level"):
        Settings(_env_file=None, log_level="not-a-level")


def test_log_level_is_normalized_to_uppercase() -> None:
    settings = Settings(_env_file=None, log_level="debug")
    assert settings.log_level == "DEBUG"
