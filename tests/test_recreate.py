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

    # The captured fixture's volume mount has Mode "z" (an SELinux relabel
    # flag) — real evidence, not a guess, that Docker can report this even
    # for a mount made via the modern Mounts API rather than only legacy
    # `-v`. It's carried over as a legacy `Binds`-style string instead of a
    # Mount object (see _build_mounts()'s docstring for why), so it shows up
    # in `volumes`, not `mounts`.
    assert "/vol-dst" not in by_target
    assert spec.create_kwargs["volumes"] == ["lookout-test-vol:/vol-dst:rw,z"]


def _container_with_mounts(mounts: list[dict[str, object]]) -> Container:
    return Container(
        id="abc123",
        name="selinux-relabel-test",
        image_id="sha256:old",
        image_name="myapp:latest",
        labels={},
        inspect={"Config": {}, "HostConfig": {}, "Mounts": mounts},
    )


def test_selinux_relabel_fixture_binds_and_mounts_split_correctly() -> None:
    # Captured live against a real SELinux-enforcing daemon (RHEL 9,
    # `getenforce` == Enforcing) -- confirmed the recreated container
    # preserves correct SELinux behavior: the "rw,z" bind readable and
    # writable, the "ro,Z" bind readable but not writable, the plain
    # unlabeled bind still denied by SELinux (unchanged), and the "z"
    # volume readable and writable. This fixture's volume mount is also
    # what originally caught a real docker-py bug in the high-level
    # containers.create(volumes=[...]) path, which fabricates garbage
    # anonymous volumes for any compound bind mode like "rw,z" -- see
    # DockerPyClient._create()'s docstring in docker/client.py.
    container = load("selinux-relabel")

    spec = build_create_kwargs(container, "sha256:newimage")

    mounts: list[Mount] = spec.create_kwargs["mounts"]
    assert [m["Target"] for m in mounts] == ["/plain"]
    assert mounts[0]["ReadOnly"] is True

    binds = spec.create_kwargs["volumes"]
    by_dest = {b.split(":")[1]: b for b in binds}
    assert by_dest["/shared"].endswith(":/shared:rw,z")
    assert "bind-shared" in by_dest["/shared"]
    assert by_dest["/private"].endswith(":/private:ro,Z")
    assert "bind-private" in by_dest["/private"]
    assert by_dest["/vol"] == "lookout-selinux-vol:/vol:rw,z"


def test_selinux_shared_relabel_bind_mount_goes_through_legacy_binds() -> None:
    # Hand-built (no live daemon available here): a bind mount whose Mode is
    # just "z" (shared SELinux label), analogous to the real fixture's
    # volume mount but for a bind, and read-write.
    container = _container_with_mounts(
        [
            {
                "Type": "bind",
                "Source": "/host/shared",
                "Destination": "/data",
                "Mode": "z",
                "RW": True,
            }
        ]
    )

    spec = build_create_kwargs(container, "sha256:newimage")

    assert "mounts" not in spec.create_kwargs
    assert spec.create_kwargs["volumes"] == ["/host/shared:/data:rw,z"]


def test_selinux_private_relabel_readonly_bind_mount_goes_through_legacy_binds() -> None:
    # "Z" (private label) combined with a read-only mount — confirms the
    # rw/ro token is re-derived from RW rather than trusted from Mode, since
    # here Mode only has "ro" alongside "Z", not "rw".
    container = _container_with_mounts(
        [
            {
                "Type": "bind",
                "Source": "/host/priv",
                "Destination": "/priv",
                "Mode": "ro,Z",
                "RW": False,
            }
        ]
    )

    spec = build_create_kwargs(container, "sha256:newimage")

    assert "mounts" not in spec.create_kwargs
    assert spec.create_kwargs["volumes"] == ["/host/priv:/priv:ro,Z"]


def test_selinux_relabel_volume_mount_goes_through_legacy_binds() -> None:
    # Named volume (not a bind) with just "Z" and no explicit rw/ro token in
    # Mode, read-only — confirms the source resolves to the volume Name
    # (not Source) on this path too, matching _build_mounts()'s non-relabel
    # branch.
    container = _container_with_mounts(
        [
            {
                "Type": "volume",
                "Name": "priv-vol",
                "Source": "/var/lib/docker/volumes/priv-vol/_data",
                "Destination": "/vol",
                "Mode": "Z",
                "RW": False,
            }
        ]
    )

    spec = build_create_kwargs(container, "sha256:newimage")

    assert "mounts" not in spec.create_kwargs
    assert spec.create_kwargs["volumes"] == ["priv-vol:/vol:ro,Z"]


def test_mount_without_relabel_flag_is_unaffected_by_the_split() -> None:
    # A plain "ro" Mode (no z/Z) must still go through the ordinary Mount
    # path, not be swept into binds by mistake.
    container = _container_with_mounts(
        [
            {
                "Type": "bind",
                "Source": "/host/plain",
                "Destination": "/plain",
                "Mode": "ro",
                "RW": False,
            }
        ]
    )

    spec = build_create_kwargs(container, "sha256:newimage")

    assert "volumes" not in spec.create_kwargs
    mounts: list[Mount] = spec.create_kwargs["mounts"]
    assert mounts[0]["Target"] == "/plain"
    assert mounts[0]["ReadOnly"] is True


def test_mount_with_missing_mode_field_is_unaffected_by_the_split() -> None:
    # Defensive case: a mount entry with no "Mode" key at all (not every
    # Docker version/mount necessarily includes it) must not crash and must
    # not be misrouted to binds.
    container = _container_with_mounts(
        [{"Type": "bind", "Source": "/host/nomode", "Destination": "/nomode", "RW": True}]
    )

    spec = build_create_kwargs(container, "sha256:newimage")

    assert "volumes" not in spec.create_kwargs
    assert spec.create_kwargs["mounts"][0]["Target"] == "/nomode"


def test_mixed_relabeled_and_plain_mounts_split_correctly() -> None:
    # Integration-style check: a plain bind, a relabeled bind, and a
    # relabeled volume together must land in the right kwarg, without one
    # group clobbering the other.
    container = _container_with_mounts(
        [
            {
                "Type": "bind",
                "Source": "/host/plain",
                "Destination": "/plain",
                "Mode": "ro",
                "RW": False,
            },
            {
                "Type": "bind",
                "Source": "/host/shared",
                "Destination": "/shared",
                "Mode": "z",
                "RW": True,
            },
            {
                "Type": "volume",
                "Name": "priv-vol",
                "Destination": "/vol",
                "Mode": "Z",
                "RW": True,
            },
        ]
    )

    spec = build_create_kwargs(container, "sha256:newimage")

    mounts: list[Mount] = spec.create_kwargs["mounts"]
    assert [m["Target"] for m in mounts] == ["/plain"]
    assert spec.create_kwargs["volumes"] == [
        "/host/shared:/shared:rw,z",
        "priv-vol:/vol:rw,Z",
    ]


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


def test_resource_limits_are_carried_over() -> None:
    # Hand-built stand-in (no live daemon available here) for HostConfig's
    # resource-limit fields.
    container = Container(
        id="abc123",
        name="resource-limits-test",
        image_id="sha256:old",
        image_name="myapp:latest",
        labels={},
        inspect={
            "Config": {},
            "HostConfig": {
                "Memory": 134217728,
                "NanoCpus": 500000000,
                "CpuShares": 512,
                "MemorySwap": -1,
                "PidsLimit": 100,
            },
        },
    )

    spec = build_create_kwargs(container, "sha256:newimage")

    assert spec.create_kwargs["mem_limit"] == 134217728
    assert spec.create_kwargs["nano_cpus"] == 500000000
    assert spec.create_kwargs["cpu_shares"] == 512
    assert spec.create_kwargs["memswap_limit"] == -1
    assert spec.create_kwargs["pids_limit"] == 100


def test_unset_resource_limits_are_not_carried_over() -> None:
    container = Container(
        id="abc123",
        name="no-resource-limits-test",
        image_id="sha256:old",
        image_name="myapp:latest",
        labels={},
        inspect={
            "Config": {},
            "HostConfig": {
                "Memory": 0,
                "NanoCpus": 0,
                "CpuShares": 0,
                "MemorySwap": 0,
                "PidsLimit": None,
            },
        },
    )

    spec = build_create_kwargs(container, "sha256:newimage")

    for key in ("mem_limit", "nano_cpus", "cpu_shares", "memswap_limit", "pids_limit"):
        assert key not in spec.create_kwargs


def test_log_config_security_and_process_options_are_carried_over() -> None:
    # Hand-built stand-in (no live daemon available here) for the remaining
    # HostConfig/Config fields covered by this mapping.
    container = Container(
        id="abc123",
        name="host-extras2-test",
        image_id="sha256:old",
        image_name="myapp:latest",
        labels={},
        inspect={
            "Config": {"StopSignal": "SIGUSR1", "StopTimeout": 30},
            "HostConfig": {
                "LogConfig": {"Type": "json-file", "Config": {"max-size": "10m"}},
                "SecurityOpt": ["no-new-privileges"],
                "GroupAdd": ["audio"],
                "ReadonlyRootfs": True,
                "ShmSize": 134217728,
                "Init": True,
                "PidMode": "host",
                "IpcMode": "shareable",
            },
        },
    )

    spec = build_create_kwargs(container, "sha256:newimage")

    assert spec.create_kwargs["stop_signal"] == "SIGUSR1"
    assert spec.create_kwargs["stop_timeout"] == 30
    assert dict(spec.create_kwargs["log_config"])["Type"] == "json-file"
    assert dict(spec.create_kwargs["log_config"])["Config"] == {"max-size": "10m"}
    assert spec.create_kwargs["security_opt"] == ["no-new-privileges"]
    assert spec.create_kwargs["group_add"] == ["audio"]
    assert spec.create_kwargs["read_only"] is True
    assert spec.create_kwargs["shm_size"] == 134217728
    assert spec.create_kwargs["init"] is True
    assert spec.create_kwargs["pid_mode"] == "host"
    assert spec.create_kwargs["ipc_mode"] == "shareable"


def test_default_ipc_mode_private_is_not_carried_over() -> None:
    container = Container(
        id="abc123",
        name="default-ipc-test",
        image_id="sha256:old",
        image_name="myapp:latest",
        labels={},
        inspect={"Config": {}, "HostConfig": {"IpcMode": "private"}},
    )

    spec = build_create_kwargs(container, "sha256:newimage")

    assert "ipc_mode" not in spec.create_kwargs


def test_static_ip_mac_fixture_survives_recreate() -> None:
    # Captured live against a real Docker daemon (RHEL 9): a container on a
    # custom network with a static IPv4 (--ip), static IPv6 (--ip6), and
    # custom MAC address (--mac-address). Confirmed live that after a real
    # stop/recreate/start cycle via DockerClient.recreate(), the new
    # container's IPv4Address, IPv6Address, MacAddress, and network alias
    # all matched the original exactly -- this also confirmed docker-py's
    # Network.connect() does accept and apply the forwarded mac_address
    # kwarg (previously only verified by reading docker-py's source, not
    # exercised against a real daemon).
    container = load("static-ip-mac")

    spec = build_create_kwargs(container, "sha256:newimage")

    attachment = spec.networks[0]
    assert attachment.name == "lookout-static-net"
    assert attachment.aliases == ["static-alias"]
    assert attachment.ipv4_address == "172.28.0.42"
    assert attachment.ipv6_address == "fd00:dead:beef::42"
    assert attachment.mac_address == "02:42:ac:11:00:2a"


def test_static_ip_and_mac_address_are_carried_over() -> None:
    # Hand-built stand-in (no live daemon available here) for a network with
    # a pinned IPAMConfig and MacAddress in NetworkSettings.Networks.
    container = Container(
        id="abc123",
        name="static-ip-test",
        image_id="sha256:old",
        image_name="myapp:latest",
        labels={},
        inspect={
            "Config": {},
            "HostConfig": {"NetworkMode": "lookout-test-net"},
            "NetworkSettings": {
                "Networks": {
                    "lookout-test-net": {
                        "Aliases": ["fixture-alias"],
                        "IPAMConfig": {
                            "IPv4Address": "172.18.0.42",
                            "IPv6Address": "fd00::42",
                        },
                        "MacAddress": "02:42:ac:12:00:2a",
                    }
                }
            },
        },
    )

    spec = build_create_kwargs(container, "sha256:newimage")

    attachment = spec.networks[0]
    assert attachment.ipv4_address == "172.18.0.42"
    assert attachment.ipv6_address == "fd00::42"
    assert attachment.mac_address == "02:42:ac:12:00:2a"


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
