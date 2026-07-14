from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, sourced from LOOKOUT_* env vars (CLI flags override)."""

    model_config = SettingsConfigDict(env_prefix="LOOKOUT_", env_file=".env", extra="ignore")

    interval_seconds: int = Field(default=300, description="Poll interval, in seconds")
    cron_schedule: str | None = Field(
        default=None, description="Cron expression; overrides interval"
    )

    include_names: list[str] = Field(default_factory=list)
    exclude_names: list[str] = Field(default_factory=list)
    label_enable: bool = Field(
        default=False, description="Only monitor containers with the enable label"
    )

    cleanup: bool = Field(
        default=False, description="Remove dangling images after a successful update"
    )
    monitor_only: bool = Field(default=False, description="Report staleness but never update")
    no_pull: bool = Field(
        default=False, description="Never pull; only recreate from images already present"
    )

    stop_timeout_seconds: int = Field(default=10)

    notification_urls: list[str] = Field(
        default_factory=list, description="Apprise-format notification URLs"
    )
    notify_only_on_change: bool = Field(
        default=False,
        description="Skip sending a notification when nothing was updated, failed, or found stale",
    )
    notify_on_startup: bool = Field(
        default=False,
        description="Send a one-time notification when lookout starts, separate from the "
        "per-run summary",
    )

    docker_host: str | None = Field(
        default=None, description="Defaults to the docker-py default (env/socket)"
    )

    registry_host: str | None = Field(
        default=None,
        description="Registry host the username/password fallback below applies to (e.g. "
        "registry.example.com). Required for the fallback to be used at all — without it, "
        "credentials would otherwise be sent to every registry with no config.json entry, "
        "including public ones like Docker Hub.",
    )
    registry_username: str | None = Field(
        default=None,
        description="Fallback registry credentials for registry_host, used only when config.json "
        "has no matching entry",
    )
    registry_password: str | None = Field(default=None)

    log_level: str = Field(default="INFO")
