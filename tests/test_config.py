from lookout.config import Settings


def test_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.interval_seconds == 300
    assert settings.cleanup is False


def test_env_prefix(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("LOOKOUT_INTERVAL_SECONDS", "60")
    settings = Settings(_env_file=None)
    assert settings.interval_seconds == 60
