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
