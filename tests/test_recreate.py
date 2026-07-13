import json
from pathlib import Path

from docker.types import Healthcheck, Mount

from lookout.docker.container import Container
from lookout.docker.recreate import build_create_kwargs

FIXTURES = Path(__file__).parent / "fixtures" / "inspect"


def load(name: str) -> Container:
    data = json.loads((FIXTURES / f"{name}.json").read_text())
    return Container.from_inspect(data)


def test_minimal_container_has_no_extras() -> None:
    container = load("minimal")

    spec = build_create_kwargs(container, "sha256:newimage")

    assert spec.create_kwargs["image"] == "sha256:newimage"
    assert spec.create_kwargs["name"] == "lookout-recreate-minimal"
    assert spec.create_kwargs["command"] == ["sleep", "3600"]
    assert "hostname" not in spec.create_kwargs  # auto-generated, equals container id prefix
    assert "restart_policy" not in spec.create_kwargs  # "no" is the default, not explicit
    assert "mounts" not in spec.create_kwargs
    assert "healthcheck" not in spec.create_kwargs
    assert "network_mode" not in spec.create_kwargs  # plain default bridge
    assert spec.networks == []


def test_comprehensive_container_env_and_labels() -> None:
    container = load("comprehensive")

    spec = build_create_kwargs(container, "sha256:newimage")

    assert "FOO=bar" in spec.create_kwargs["environment"]
    assert "BAZ=qux" in spec.create_kwargs["environment"]
    assert spec.create_kwargs["labels"] == {
        "com.example.label1": "value1",
        "com.example.label2": "value2",
    }


def test_comprehensive_container_hostname_explicit() -> None:
    container = load("comprehensive")
    spec = build_create_kwargs(container, "sha256:newimage")
    assert spec.create_kwargs["hostname"] == "fixture-host"


def test_comprehensive_container_bind_and_named_volume_mounts() -> None:
    container = load("comprehensive")

    spec = build_create_kwargs(container, "sha256:newimage")

    mounts: list[Mount] = spec.create_kwargs["mounts"]
    by_target = {m["Target"]: m for m in mounts}

    bind = by_target["/bind-dst"]
    assert bind["Type"] == "bind"
    assert bind["Source"].endswith("bind-src")
    assert bind["ReadOnly"] is True

    volume = by_target["/vol-dst"]
    assert volume["Type"] == "volume"
    assert volume["Source"] == "lookout-test-vol"
    assert volume["ReadOnly"] is False


def test_comprehensive_container_restart_policy() -> None:
    container = load("comprehensive")
    spec = build_create_kwargs(container, "sha256:newimage")
    assert spec.create_kwargs["restart_policy"] == {
        "Name": "unless-stopped",
        "MaximumRetryCount": 0,
    }


def test_comprehensive_container_healthcheck() -> None:
    container = load("comprehensive")

    spec = build_create_kwargs(container, "sha256:newimage")

    hc: Healthcheck = spec.create_kwargs["healthcheck"]
    assert hc["Test"] == ["CMD-SHELL", "echo ok"]
    assert hc["Interval"] == 5_000_000_000
    assert hc["Retries"] == 2


def test_comprehensive_container_networks_deferred_to_post_create() -> None:
    container = load("comprehensive")

    spec = build_create_kwargs(container, "sha256:newimage")

    assert "network_mode" not in spec.create_kwargs  # default bridge auto-attaches, then swapped
    names = {n.name: n.aliases for n in spec.networks}
    assert names["lookout-test-net"] == ["fixture-alias"]
    assert names["lookout-test-net2"] == ["fixture-alias2"]


def test_comprehensive_container_caps_and_ports() -> None:
    container = load("comprehensive")

    spec = build_create_kwargs(container, "sha256:newimage")

    assert spec.create_kwargs["cap_add"] == ["CAP_NET_ADMIN"]
    assert spec.create_kwargs["ports"] == {"80/tcp": "18080"}
