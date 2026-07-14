"""Rebuild create-kwargs for a container from its existing inspect data.

Riskiest module in the project — every mapping here is verified against a
real `docker inspect` fixture in tests/fixtures/inspect/ (see
tests/test_recreate.py) rather than assumed from the API docs alone.

Known simplifications, not yet handled:
- Non-bridge/custom NetworkMode values (host, container:<name>, ...) are
  passed through as network_mode but never validated against a live daemon.
  (`--net=container:<id>` refs are resolved to `container:<name>` by
  DockerClient before reaching this module — see client.py.)
- Ephemeral host ports published with `-P` come back pinned to whatever host
  port they'd previously been assigned, rather than getting a fresh one on
  recreate. This isn't a lookout gap to close: `docker inspect`'s
  PortBindings only ever records the concrete host port a container ended up
  with, with no way to tell in hindsight whether it came from `-P` or a fixed
  `-p hostport:containerport`. Watchtower has the exact same behavior for the
  same reason (it replays the original PortBindings verbatim too).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from docker.types import Healthcheck, LogConfig, Mount, Ulimit

from lookout.docker.container import Container

_DEFAULT_NETWORK_MODES = {"default", "bridge", "host", "none"}


@dataclass
class NetworkAttachment:
    name: str
    aliases: list[str] = field(default_factory=list)
    ipv4_address: str | None = None
    ipv6_address: str | None = None
    mac_address: str | None = None


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
    if config.get("StopSignal"):
        kwargs["stop_signal"] = config["StopSignal"]
    if config.get("StopTimeout") is not None:
        kwargs["stop_timeout"] = config["StopTimeout"]

    hostname = config.get("Hostname")
    if hostname and not container.id.startswith(hostname):
        kwargs["hostname"] = hostname

    mounts, binds = _build_mounts(inspect.get("Mounts") or [])
    if mounts:
        kwargs["mounts"] = mounts
    if binds:
        kwargs["volumes"] = binds

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

    log_config = host_config.get("LogConfig") or {}
    if log_config.get("Type"):
        kwargs["log_config"] = LogConfig(
            Type=log_config["Type"], Config=log_config.get("Config") or {}
        )
    if host_config.get("SecurityOpt"):
        kwargs["security_opt"] = host_config["SecurityOpt"]
    if host_config.get("GroupAdd"):
        kwargs["group_add"] = host_config["GroupAdd"]
    if host_config.get("ReadonlyRootfs"):
        kwargs["read_only"] = True
    if host_config.get("ShmSize"):
        kwargs["shm_size"] = host_config["ShmSize"]
    if host_config.get("Init") is not None:
        kwargs["init"] = host_config["Init"]
    if host_config.get("PidMode"):
        kwargs["pid_mode"] = host_config["PidMode"]
    # "private" is IpcMode's own reported default when nothing custom was
    # set, same treatment as RestartPolicy's "no" above.
    ipc_mode = host_config.get("IpcMode")
    if ipc_mode and ipc_mode != "private":
        kwargs["ipc_mode"] = ipc_mode

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


_RELABEL_TOKENS = {"z", "Z"}


def _build_mounts(raw_mounts: list[dict[str, Any]]) -> tuple[list[Mount], list[str]]:
    """Returns (mounts, binds).

    Mounts using the SELinux relabel flag (`:z`/`:Z`) can't go through the
    modern Mount type used for everything else here — the Docker Engine API's
    Mount spec (BindOptions/VolumeOptions) has no field for it at all, since
    that flag is a legacy `-v`/`Binds`-only concept. Docker does accept the
    legacy `Binds` and modern `Mounts` HostConfig fields in the same
    create() call though (they populate independent HostConfig keys, the
    same way a real `docker run` mixing `-v` and `--mount` would), so
    relabeled mounts are carried over as `Binds`-style strings in a separate
    list rather than being dropped.
    """
    mounts: list[Mount] = []
    binds: list[str] = []
    for m in raw_mounts:
        mount_type = m.get("Type", "volume")
        if mount_type not in _SUPPORTED_MOUNT_TYPES:
            # tmpfs (and anything else Docker might report here) isn't
            # carried over — skip explicitly rather than relying on the
            # resulting empty source being harmlessly falsy past docker-py's
            # own tmpfs handling.
            continue
        source = m.get("Name") if mount_type == "volume" else m.get("Source")
        mode_tokens = (m.get("Mode") or "").split(",")
        relabel_tokens = [t for t in mode_tokens if t in _RELABEL_TOKENS]
        if relabel_tokens:
            # Re-derive rw/ro from RW rather than trusting a rw/ro token in
            # Mode (a Mode of just "z" with no explicit rw/ro token defaults
            # to rw at the Binds-string level, but RW is the authoritative
            # field either way and every fixture seen so far agrees with it).
            rw_token = "rw" if m.get("RW", True) else "ro"
            full_mode = ",".join([rw_token, *relabel_tokens])
            binds.append(f"{source}:{m['Destination']}:{full_mode}")
            continue
        mounts.append(
            Mount(
                target=m["Destination"],
                source=source,
                type=mount_type,
                read_only=not m.get("RW", True),
            )
        )
    return mounts, binds


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
    attachments = []
    for name, cfg in networks.items():
        ipam = cfg.get("IPAMConfig") or {}
        attachments.append(
            NetworkAttachment(
                name=name,
                aliases=list(cfg.get("Aliases") or []),
                ipv4_address=ipam.get("IPv4Address") or None,
                ipv6_address=ipam.get("IPv6Address") or None,
                mac_address=cfg.get("MacAddress") or None,
            )
        )
    return attachments
