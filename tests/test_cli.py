from __future__ import annotations

from typing import Any

from click.testing import CliRunner

import lookout.cli as cli_module
from lookout import __version__
from lookout.core.session import Session


class FakeDockerClient:
    def __init__(self, docker_host: str | None = None, swarm_active: bool = False) -> None:
        self.docker_host = docker_host
        self.swarm_active = swarm_active

    def is_swarm_active(self) -> bool:
        return self.swarm_active


class FakeRegistryClient:
    pass


def test_version_flag_prints_version_and_exits() -> None:
    result = CliRunner().invoke(cli_module.main, ["--version"])

    assert result.exit_code == 0
    assert __version__ in result.output


def test_include_exclude_split_comma_separated_values(monkeypatch: Any) -> None:
    # Regression test: LOOKOUT_INCLUDE_NAMES=a,b (env) splits on commas, but
    # --include a,b (CLI, single flag) used to produce the literal name
    # "a,b" that matches nothing -- a silent footgun for anyone using the
    # comma spelling out of habit from the env var. Both spellings must now
    # produce the same result.
    monkeypatch.setattr(cli_module, "DockerPyClient", FakeDockerClient)
    monkeypatch.setattr(cli_module, "RegistryClient", FakeRegistryClient)

    captured_settings = []
    monkeypatch.setattr(
        cli_module,
        "run_update",
        lambda dc, rc, settings: captured_settings.append(settings) or Session(),
    )
    monkeypatch.setattr(
        cli_module, "send_notifications", lambda session, urls, only_on_change=False: None
    )

    runner = CliRunner()
    result = runner.invoke(
        cli_module.main,
        ["--run-once", "--include", "a,b", "--exclude", "c, d"],
    )

    assert result.exit_code == 0, result.output
    settings = captured_settings[0]
    assert settings.include_names == ["a", "b"]
    assert settings.exclude_names == ["c", "d"]


def test_run_once_wires_flags_into_settings(monkeypatch: Any) -> None:
    monkeypatch.setattr(cli_module, "DockerPyClient", FakeDockerClient)
    monkeypatch.setattr(cli_module, "RegistryClient", FakeRegistryClient)

    captured_settings = []
    monkeypatch.setattr(
        cli_module,
        "run_update",
        lambda dc, rc, settings: captured_settings.append(settings) or Session(),
    )
    monkeypatch.setattr(
        cli_module, "send_notifications", lambda session, urls, only_on_change=False: None
    )

    runner = CliRunner()
    result = runner.invoke(
        cli_module.main,
        [
            "--run-once",
            "--include",
            "a",
            "--include",
            "b",
            "--exclude",
            "c",
            "--cleanup",
            "--monitor-only",
            "--no-pull",
            "--label-enable",
            "--notify-only-on-change",
            "--notify-on-startup",
        ],
    )

    assert result.exit_code == 0, result.output
    assert len(captured_settings) == 1
    settings = captured_settings[0]
    assert settings.include_names == ["a", "b"]
    assert settings.exclude_names == ["c"]
    assert settings.cleanup is True
    assert settings.monitor_only is True
    assert settings.no_pull is True
    assert settings.label_enable is True
    assert settings.notify_only_on_change is True
    assert settings.notify_on_startup is True


def test_omitted_flags_leave_settings_defaults(monkeypatch: Any) -> None:
    monkeypatch.setattr(cli_module, "DockerPyClient", FakeDockerClient)
    monkeypatch.setattr(cli_module, "RegistryClient", FakeRegistryClient)

    captured_settings = []
    monkeypatch.setattr(
        cli_module,
        "run_update",
        lambda dc, rc, settings: captured_settings.append(settings) or Session(),
    )
    monkeypatch.setattr(
        cli_module, "send_notifications", lambda session, urls, only_on_change=False: None
    )

    runner = CliRunner()
    result = runner.invoke(cli_module.main, ["--run-once"])

    assert result.exit_code == 0, result.output
    settings = captured_settings[0]
    assert settings.cleanup is False
    assert settings.monitor_only is False
    assert settings.include_names == []
    assert settings.notify_only_on_change is False
    assert settings.notify_on_startup is False


def test_interval_mode_invokes_scheduler_with_parsed_interval(monkeypatch: Any) -> None:
    monkeypatch.setattr(cli_module, "DockerPyClient", FakeDockerClient)
    monkeypatch.setattr(cli_module, "RegistryClient", FakeRegistryClient)
    monkeypatch.setattr(cli_module, "run_update", lambda dc, rc, settings: Session())
    monkeypatch.setattr(
        cli_module, "send_notifications", lambda session, urls, only_on_change=False: None
    )

    captured = {}

    def fake_run_forever(job: Any, interval_seconds: int) -> None:
        captured["interval_seconds"] = interval_seconds
        job()  # simulate a single tick so job wiring is exercised too

    monkeypatch.setattr(cli_module, "run_forever", fake_run_forever)

    runner = CliRunner()
    result = runner.invoke(cli_module.main, ["--interval", "42"])

    assert result.exit_code == 0, result.output
    assert captured["interval_seconds"] == 42


def test_notify_on_startup_calls_send_startup_once(monkeypatch: Any) -> None:
    monkeypatch.setattr(cli_module, "DockerPyClient", FakeDockerClient)
    monkeypatch.setattr(cli_module, "RegistryClient", FakeRegistryClient)
    monkeypatch.setattr(cli_module, "run_update", lambda dc, rc, settings: Session())
    monkeypatch.setattr(
        cli_module, "send_notifications", lambda session, urls, only_on_change=False: None
    )

    startup_calls: list[list[str]] = []
    monkeypatch.setattr(cli_module, "send_startup", lambda urls: startup_calls.append(urls))

    runner = CliRunner()
    result = runner.invoke(cli_module.main, ["--run-once", "--notify-on-startup"])

    assert result.exit_code == 0, result.output
    assert len(startup_calls) == 1


def test_run_once_succeeds_despite_notification_send_failure(monkeypatch: Any) -> None:
    # Regression test: the update work (run_update) already completed by the
    # time notifications are sent -- a bug in the notification-delivery
    # library shouldn't turn an otherwise-successful --run-once pass into a
    # nonzero exit.
    monkeypatch.setattr(cli_module, "DockerPyClient", FakeDockerClient)
    monkeypatch.setattr(cli_module, "RegistryClient", FakeRegistryClient)
    monkeypatch.setattr(cli_module, "run_update", lambda dc, rc, settings: Session())

    def failing_send(session: Any, urls: Any, only_on_change: bool = False) -> None:
        raise RuntimeError("notification backend exploded")

    monkeypatch.setattr(cli_module, "send_notifications", failing_send)

    runner = CliRunner()
    result = runner.invoke(cli_module.main, ["--run-once"])

    assert result.exit_code == 0, result.output


def test_run_once_succeeds_despite_startup_notification_failure(monkeypatch: Any) -> None:
    monkeypatch.setattr(cli_module, "DockerPyClient", FakeDockerClient)
    monkeypatch.setattr(cli_module, "RegistryClient", FakeRegistryClient)
    monkeypatch.setattr(cli_module, "run_update", lambda dc, rc, settings: Session())
    monkeypatch.setattr(
        cli_module, "send_notifications", lambda session, urls, only_on_change=False: None
    )

    def failing_send_startup(urls: Any) -> None:
        raise RuntimeError("notification backend exploded")

    monkeypatch.setattr(cli_module, "send_startup", failing_send_startup)

    runner = CliRunner()
    result = runner.invoke(cli_module.main, ["--run-once", "--notify-on-startup"])

    assert result.exit_code == 0, result.output


def test_swarm_active_logs_warning_but_still_runs(
    monkeypatch: Any, caplog: Any
) -> None:
    # Regression test for a real Watchtower trap: a Swarm-enabled daemon
    # produced a clean, error-free poll that updated nothing at all, for
    # weeks, before anyone traced it back to Swarm. lookout must not repeat
    # that silently -- but it also shouldn't refuse to start, since
    # standalone containers on a Swarm-enabled daemon are still fair game.
    monkeypatch.setattr(
        cli_module, "DockerPyClient", lambda docker_host=None: FakeDockerClient(swarm_active=True)
    )
    monkeypatch.setattr(cli_module, "RegistryClient", FakeRegistryClient)
    run_calls: list[object] = []
    monkeypatch.setattr(
        cli_module, "run_update", lambda dc, rc, settings: run_calls.append(1) or Session()
    )
    monkeypatch.setattr(
        cli_module, "send_notifications", lambda session, urls, only_on_change=False: None
    )

    with caplog.at_level("WARNING", logger="lookout.cli"):
        result = CliRunner().invoke(cli_module.main, ["--run-once"])

    assert result.exit_code == 0, result.output
    assert len(run_calls) == 1
    assert any("Swarm" in record.message for record in caplog.records)


def test_swarm_inactive_logs_no_warning(monkeypatch: Any, caplog: Any) -> None:
    monkeypatch.setattr(cli_module, "DockerPyClient", FakeDockerClient)
    monkeypatch.setattr(cli_module, "RegistryClient", FakeRegistryClient)
    monkeypatch.setattr(cli_module, "run_update", lambda dc, rc, settings: Session())
    monkeypatch.setattr(
        cli_module, "send_notifications", lambda session, urls, only_on_change=False: None
    )

    with caplog.at_level("WARNING", logger="lookout.cli"):
        result = CliRunner().invoke(cli_module.main, ["--run-once"])

    assert result.exit_code == 0, result.output
    assert not any("Swarm" in record.message for record in caplog.records)


def test_notify_on_startup_not_called_when_flag_omitted(monkeypatch: Any) -> None:
    monkeypatch.setattr(cli_module, "DockerPyClient", FakeDockerClient)
    monkeypatch.setattr(cli_module, "RegistryClient", FakeRegistryClient)
    monkeypatch.setattr(cli_module, "run_update", lambda dc, rc, settings: Session())
    monkeypatch.setattr(
        cli_module, "send_notifications", lambda session, urls, only_on_change=False: None
    )

    startup_calls: list[list[str]] = []
    monkeypatch.setattr(cli_module, "send_startup", lambda urls: startup_calls.append(urls))

    runner = CliRunner()
    result = runner.invoke(cli_module.main, ["--run-once"])

    assert result.exit_code == 0, result.output
    assert startup_calls == []
