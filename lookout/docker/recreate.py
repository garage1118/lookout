"""Rebuild create-kwargs for a container from its existing inspect data.

Riskiest module in the project — every mapping here is verified against a
real `docker inspect` fixture in tests/fixtures/inspect/ (see
tests/test_recreate.py) rather than assumed from the API docs alone.

Known simplifications, not yet handled:
- SELinux mount relabeling ('z'/'Z' bind mode) is dropped.
- Non-bridge/custom NetworkMode values (host, container:<name>, ...) are
  passed through as network_mode but never validated against a live daemon.
  (`--net=container:<id>` refs are resolved to `container:<name>` by
  DockerClient before reaching this module — see client.py.)
- LogConfig (driver + options), SecurityOpt, GroupAdd, ReadonlyRootfs,
  ShmSize, Init, StopSignal/StopTimeout, and PidMode/IpcMode are not
  carried over.
- Per-network static IPs (IPAMConfig.IPv4Address) and MAC addresses are
  dropped by _build_networks() — aliases are kept, but a container with a
  pinned IP comes back with a dynamic one.
- Ephemeral host ports published with `-P` are pinned to their previously
  assigned host port by _build_ports(), rather than getting a fresh
  ephemeral port on recreate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from docker.types import Healthcheck, Mount, Ulimit

from lookout.docker.container import Container

_DEFAULT_NETWORK_MODES = {"default", "bridge", "host", "none"}


@dataclass
class NetworkAttachment:
    name: str
    aliases: list[str] = field(default_factory=list)


@dataclass
class RecreateSpec:
    """create_kwargs go to containers.create(); when `networks` is non-empty
    the created container will be on the default bridge network and the
    caller must disconnect that and connect each entry in `networks`
    (including the original primary) so per-network aliases survive — the
    create()-time `network=` shortcut doesn't support aliases, and Docker
    won't let extra networks attach to a container created in "none" mode."""

    create_kwargs: dict[str, Any]
    networks: list[NetworkAttachment] = field(default_factory=list)


def build_create_kwargs(container: Container, new_image_id: str) -> RecreateSpec:
    inspect = container.inspect
    config = inspect.get("Config") or {}
    host_config = inspect.get("HostConfig") or {}

    kwargs: dict[str, Any] = {
        "image": new_image_id,
        "name": container.name,
        "detach": True,
    }

    if config.get("Cmd"):
        kwargs["command"] = config["Cmd"]
    if config.get("Entrypoint"):
        kwargs["entrypoint"] = config["Entrypoint"]
    if config.get("Env"):
        kwargs["environment"] = config["Env"]
    if config.get("Labels"):
        kwargs["labels"] = config["Labels"]
    if config.get("WorkingDir"):
        kwargs["working_dir"] = config["WorkingDir"]
    if config.get("User"):
        kwargs["user"] = config["User"]

    hostname = config.get("Hostname")
    if hostname and not container.id.startswith(hostname):
        kwargs["hostname"] = hostname

    mounts = _build_mounts(inspect.get("Mounts") or [])
    if mounts:
        kwargs["mounts"] = mounts

    restart_policy = host_config.get("RestartPolicy") or {}
    if restart_policy.get("Name") and restart_policy["Name"] != "no":
        kwargs["restart_policy"] = restart_policy

    if host_config.get("CapAdd"):
        kwargs["cap_add"] = host_config["CapAdd"]
    if host_config.get("CapDrop"):
        kwargs["cap_drop"] = host_config["CapDrop"]
    if host_config.get("Privileged"):
        kwargs["privileged"] = True

    if host_config.get("Ulimits"):
        kwargs["ulimits"] = [Ulimit(**u) for u in host_config["Ulimits"]]
    if host_config.get("Sysctls"):
        kwargs["sysctls"] = host_config["Sysctls"]
    if host_config.get("Devices"):
        kwargs["devices"] = [
            f"{d['PathOnHost']}:{d['PathInContainer']}:{d['CgroupPermissions']}"
            for d in host_config["Devices"]
        ]
    if host_config.get("Dns"):
        kwargs["dns"] = host_config["Dns"]
    if host_config.get("ExtraHosts"):
        # HostConfig reports "host:ip" strings; create() wants a {host: ip} dict.
        kwargs["extra_hosts"] = dict(h.split(":", 1) for h in host_config["ExtraHosts"])
    if host_config.get("Tmpfs"):
        kwargs["tmpfs"] = host_config["Tmpfs"]

    if host_config.get("Memory"):
        kwargs["mem_limit"] = host_config["Memory"]
    if host_config.get("NanoCpus"):
        kwargs["nano_cpus"] = host_config["NanoCpus"]
    if host_config.get("CpuShares"):
        kwargs["cpu_shares"] = host_config["CpuShares"]
    # 0 means "not set" (the common case); -1 means "unlimited swap", which
    # is a meaningful explicit value worth carrying over, not a default.
    memory_swap = host_config.get("MemorySwap")
    if memory_swap:
        kwargs["memswap_limit"] = memory_swap
    if host_config.get("PidsLimit") is not None:
        kwargs["pids_limit"] = host_config["PidsLimit"]

    healthcheck = _build_healthcheck(config.get("Healthcheck"))
    if healthcheck is not None:
        kwargs["healthcheck"] = healthcheck

    ports = _build_ports(config.get("ExposedPorts") or {}, host_config.get("PortBindings") or {})
    if ports:
        kwargs["ports"] = ports

    networks = _build_networks(inspect, host_config.get("NetworkMode") or "")
    if not networks:
        # No custom networks: leave network_mode unset for a plain default
        # bridge attach, unless it's a special mode (host, container:X, ...).
        mode = host_config.get("NetworkMode")
        if mode and mode not in ("default", "bridge"):
            kwargs["network_mode"] = mode
    # else: let the default bridge auto-attach at create time; the caller
    # disconnects it and connects the real networks after, since Docker
    # rejects connecting extra networks to a container created in "none"
    # mode and create()'s `network=` param can't carry per-network aliases.

    return RecreateSpec(create_kwargs=kwargs, networks=networks)


_SUPPORTED_MOUNT_TYPES = {"bind", "volume"}


def _build_mounts(raw_mounts: list[dict[str, Any]]) -> list[Mount]:
    mounts = []
    for m in raw_mounts:
        mount_type = m.get("Type", "volume")
        if mount_type not in _SUPPORTED_MOUNT_TYPES:
            # tmpfs (and anything else Docker might report here) isn't
            # carried over — skip explicitly rather than relying on the
            # resulting empty source being harmlessly falsy past docker-py's
            # own tmpfs handling.
            continue
        source = m.get("Name") if mount_type == "volume" else m.get("Source")
        mounts.append(
            Mount(
                target=m["Destination"],
                source=source,
                type=mount_type,
                read_only=not m.get("RW", True),
            )
        )
    return mounts


def _build_healthcheck(raw: dict[str, Any] | None) -> Healthcheck | None:
    if not raw or not raw.get("Test") or raw["Test"] == ["NONE"]:
        return None
    return Healthcheck(
        test=raw["Test"],
        interval=raw.get("Interval", 0),
        timeout=raw.get("Timeout", 0),
        retries=raw.get("Retries", 0),
        start_period=raw.get("StartPeriod", 0),
    )


def _build_ports(
    exposed: dict[str, Any], bindings: dict[str, list[dict[str, str]] | None]
) -> dict[str, Any]:
    ports: dict[str, Any] = {}
    for port_proto in exposed:
        host_bindings = bindings.get(port_proto)
        if not host_bindings:
            ports[port_proto] = None
            continue
        mapped = [
            (b["HostIp"], b["HostPort"]) if b.get("HostIp") else b["HostPort"]
            for b in host_bindings
        ]
        ports[port_proto] = mapped[0] if len(mapped) == 1 else mapped
    return ports


def _build_networks(inspect: dict[str, Any], mode: str) -> list[NetworkAttachment]:
    if mode in _DEFAULT_NETWORK_MODES or mode.startswith("container:"):
        return []
    networks = inspect.get("NetworkSettings", {}).get("Networks") or {}
    return [
        NetworkAttachment(name=name, aliases=list(cfg.get("Aliases") or []))
        for name, cfg in networks.items()
    ]
