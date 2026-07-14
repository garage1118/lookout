"""Rebuild create-kwargs for a container from its existing inspect data.

Riskiest module in the project — every mapping here is verified against a
real `docker inspect` fixture in tests/fixtures/inspect/ (see
tests/test_recreate.py) rather than assumed from the API docs alone.

Known simplifications, not yet handled:
- SELinux mount relabeling ('z'/'Z' bind mode) is dropped.
- Ulimits, sysctls, devices, dns, extra_hosts, tmpfs are not carried over.
- `--net=container:<id>` and other non-bridge/custom NetworkMode values are
  passed through as network_mode but never validated against a live daemon.
- Resource limits (Memory, NanoCpus/CpuShares, MemorySwap, PidsLimit) are
  dropped, silently removing a recreated container's limits.
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

from docker.types import Healthcheck, Mount

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


def _build_mounts(raw_mounts: list[dict[str, Any]]) -> list[Mount]:
    mounts = []
    for m in raw_mounts:
        mount_type = m.get("Type", "volume")
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
