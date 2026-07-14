from __future__ import annotations

from typing import Any

from lookout.core.session import Session
from lookout.notifications.notify import send, send_startup


class FakeApprise:
    instances: list[FakeApprise] = []

    def __init__(self) -> None:
        self.added: list[str] = []
        self.add_result = True
        self.notify_result = True
        self.notified: list[dict[str, Any]] = []
        FakeApprise.instances.append(self)

    def add(self, url: str) -> bool:
        self.added.append(url)
        return self.add_result

    def notify(self, body: str, title: str) -> bool:
        self.notified.append({"body": body, "title": title})
        return self.notify_result


def test_send_does_nothing_without_urls(monkeypatch: Any) -> None:
    FakeApprise.instances = []
    monkeypatch.setattr("lookout.notifications.notify.apprise.Apprise", FakeApprise)

    send(Session(), [])

    assert FakeApprise.instances == []


def test_send_adds_each_url_and_notifies_with_summary(monkeypatch: Any) -> None:
    FakeApprise.instances = []
    monkeypatch.setattr("lookout.notifications.notify.apprise.Apprise", FakeApprise)

    session = Session()
    send(session, ["slack://token@channel", "mailto://user@example.com"])

    assert len(FakeApprise.instances) == 1
    instance = FakeApprise.instances[0]
    assert instance.added == ["slack://token@channel", "mailto://user@example.com"]
    assert instance.notified == [{"body": session.summary(), "title": "lookout run summary"}]


def test_send_logs_warning_when_url_fails_to_parse(monkeypatch: Any, caplog: Any) -> None:
    FakeApprise.instances = []
    monkeypatch.setattr("lookout.notifications.notify.apprise.Apprise", FakeApprise)

    def make_bad_apprise() -> FakeApprise:
        instance = FakeApprise()
        instance.add_result = False
        return instance

    monkeypatch.setattr("lookout.notifications.notify.apprise.Apprise", make_bad_apprise)

    with caplog.at_level("WARNING"):
        send(Session(), ["not-a-real-url"])

    assert "failed to parse notification URL" in caplog.text


def test_send_logs_warning_when_notify_fails(monkeypatch: Any, caplog: Any) -> None:
    FakeApprise.instances = []

    def make_failing_apprise() -> FakeApprise:
        instance = FakeApprise()
        instance.notify_result = False
        return instance

    monkeypatch.setattr("lookout.notifications.notify.apprise.Apprise", make_failing_apprise)

    with caplog.at_level("WARNING"):
        send(Session(), ["slack://token@channel"])

    assert "one or more notifications failed to send" in caplog.text


def test_send_skips_when_only_on_change_and_nothing_happened(monkeypatch: Any) -> None:
    FakeApprise.instances = []
    monkeypatch.setattr("lookout.notifications.notify.apprise.Apprise", FakeApprise)

    send(Session(), ["slack://token@channel"], only_on_change=True)

    assert FakeApprise.instances == []


def test_send_still_sends_when_only_on_change_and_something_happened(monkeypatch: Any) -> None:
    from lookout.docker.container import Container

    FakeApprise.instances = []
    monkeypatch.setattr("lookout.notifications.notify.apprise.Apprise", FakeApprise)

    container = Container(
        id="id-web",
        name="web",
        image_id="sha256:x",
        image_name="myapp:latest",
        labels={},
        inspect={},
    )
    session = Session(updated=[container])
    send(session, ["slack://token@channel"], only_on_change=True)

    assert len(FakeApprise.instances) == 1
    assert FakeApprise.instances[0].notified == [
        {"body": session.summary(), "title": "lookout run summary"}
    ]


def test_send_startup_does_nothing_without_urls(monkeypatch: Any) -> None:
    FakeApprise.instances = []
    monkeypatch.setattr("lookout.notifications.notify.apprise.Apprise", FakeApprise)

    send_startup([])

    assert FakeApprise.instances == []


def test_send_startup_notifies_with_fixed_message(monkeypatch: Any) -> None:
    from lookout import __version__

    FakeApprise.instances = []
    monkeypatch.setattr("lookout.notifications.notify.apprise.Apprise", FakeApprise)

    send_startup(["slack://token@channel"])

    assert len(FakeApprise.instances) == 1
    instance = FakeApprise.instances[0]
    assert instance.added == ["slack://token@channel"]
    assert instance.notified == [
        {"body": f"lookout v{__version__} started", "title": "lookout started"}
    ]


def test_send_startup_logs_warning_when_url_fails_to_parse(monkeypatch: Any, caplog: Any) -> None:
    FakeApprise.instances = []

    def make_bad_apprise() -> FakeApprise:
        instance = FakeApprise()
        instance.add_result = False
        return instance

    monkeypatch.setattr("lookout.notifications.notify.apprise.Apprise", make_bad_apprise)

    with caplog.at_level("WARNING"):
        send_startup(["not-a-real-url"])

    assert "failed to parse notification URL" in caplog.text


def test_send_startup_logs_warning_when_notify_fails(monkeypatch: Any, caplog: Any) -> None:
    FakeApprise.instances = []

    def make_failing_apprise() -> FakeApprise:
        instance = FakeApprise()
        instance.notify_result = False
        return instance

    monkeypatch.setattr("lookout.notifications.notify.apprise.Apprise", make_failing_apprise)

    with caplog.at_level("WARNING"):
        send_startup(["slack://token@channel"])

    assert "one or more notifications failed to send" in caplog.text
