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


def test_tmpfs_mount_is_not_carried_over() -> None:
    # Not a captured fixture (no live daemon available here) — a minimal,
    # hand-built stand-in for Docker's documented tmpfs entry shape in
    # `docker inspect`'s Mounts array, to pin down that unsupported mount
    # types are skipped explicitly rather than passed through with an
    # accidentally-harmless empty source.
    container = Container(
        id="abc123",
        name="tmpfs-test",
        image_id="sha256:old",
        image_name="myapp:latest",
        labels={},
        inspect={
            "Config": {},
            "HostConfig": {},
            "Mounts": [{"Type": "tmpfs", "Destination": "/tmp/scratch", "RW": True}],
        },
    )

    spec = build_create_kwargs(container, "sha256:newimage")

    assert "mounts" not in spec.create_kwargs


def test_ulimits_sysctls_devices_dns_extra_hosts_tmpfs_are_carried_over() -> None:
    # Not a captured fixture (no live daemon available here) — a minimal,
    # hand-built stand-in for the HostConfig shape these fields take in a
    # real `docker inspect` payload.
    container = Container(
        id="abc123",
        name="host-extras-test",
        image_id="sha256:old",
        image_name="myapp:latest",
        labels={},
        inspect={
            "Config": {},
            "HostConfig": {
                "Ulimits": [{"Name": "nofile", "Soft": 1024, "Hard": 2048}],
                "Sysctls": {"net.core.somaxconn": "1024"},
                "Devices": [
                    {
                        "PathOnHost": "/dev/sda",
                        "PathInContainer": "/dev/xvda",
                        "CgroupPermissions": "rwm",
                    }
                ],
                "Dns": ["8.8.8.8"],
                "ExtraHosts": ["somehost:162.242.195.82"],
                "Tmpfs": {"/tmp/scratch": "size=64m"},
            },
        },
    )

    spec = build_create_kwargs(container, "sha256:newimage")

    ulimit = spec.create_kwargs["ulimits"][0]
    assert (ulimit["Name"], ulimit["Soft"], ulimit["Hard"]) == ("nofile", 1024, 2048)
    assert spec.create_kwargs["sysctls"] == {"net.core.somaxconn": "1024"}
    assert spec.create_kwargs["devices"] == ["/dev/sda:/dev/xvda:rwm"]
    assert spec.create_kwargs["dns"] == ["8.8.8.8"]
    assert spec.create_kwargs["extra_hosts"] == {"somehost": "162.242.195.82"}
    assert spec.create_kwargs["tmpfs"] == {"/tmp/scratch": "size=64m"}


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
