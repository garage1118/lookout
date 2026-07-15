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
  recreate. `HostConfig.PublishAllPorts` does actually record whether `-P`
  was used (so this could be distinguished from a fixed
  `-p hostport:containerport` if it mattered), but it's deliberately left
  unused here: pinning the previous port is the more useful default for an
  auto-updater specifically, since anything depending on that port (a
  reverse proxy config, a firewall rule, a bookmark) would otherwise break
  on every single update instead of staying stable across them. This also
  matches Watchtower's behavior, which replays the original PortBindings
  verbatim for the same reason.
- `ExposedPorts` is copied as-is, including entries that only exist because
  the *old* image declared them via `EXPOSE` (as opposed to `Cmd`/`Env`/
  `Labels`/etc., which are subtracted against the old image's own `Config` in
  `build_create_kwargs` so the new image's defaults can take over). Harmless
  in practice -- it only affects which ports Docker reports as exposed
  metadata, not which are actually published -- so it's left unsubtracted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from docker.types import DeviceRequest, Healthcheck, LogConfig, Mount, Ulimit

from lookout.docker.container import Container

_DEFAULT_NETWORK_MODES = {"default", "bridge"}
_NO_EXTRA_NETWORKS_MODES = {"host", "none"}


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


def build_create_kwargs(
    container: Container, new_image_id: str, old_image_config: dict[str, Any] | None = None
) -> RecreateSpec:
    """`old_image_config` is the `Config` block of the image the container is
    *currently* running (not the new image) — pass it so values that are
    only sitting in the container's inspect because the old image baked
    them in as defaults (not because a user explicitly overrode them) can be
    left out of `kwargs`, letting the new image's own defaults apply
    instead. `docker inspect`'s `Config` is a merge of user-supplied options
    and the image's own defaults, with no way to tell them apart directly —
    e.g. an un-overridden `ENV PATH=...` from the image shows up identically
    to a real `-e` override. Omit it (leave `old_image_config` as `None`/
    `{}`) to fall back to copying everything verbatim, as before."""
    inspect = container.inspect
    config = inspect.get("Config") or {}
    host_config = inspect.get("HostConfig") or {}
    image_config = old_image_config or {}

    kwargs: dict[str, Any] = {
        "image": new_image_id,
        "name": container.name,
        "detach": True,
    }

    if config.get("Cmd") and config.get("Cmd") != image_config.get("Cmd"):
        kwargs["command"] = config["Cmd"]
    if config.get("Entrypoint") and config.get("Entrypoint") != image_config.get("Entrypoint"):
        kwargs["entrypoint"] = config["Entrypoint"]
    if config.get("Env"):
        old_env = set(image_config.get("Env") or [])
        env = [e for e in config["Env"] if e not in old_env]
        if env:
            kwargs["environment"] = env
    if config.get("Labels"):
        old_labels = image_config.get("Labels") or {}
        labels = {k: v for k, v in config["Labels"].items() if old_labels.get(k) != v}
        if labels:
            kwargs["labels"] = labels
    if config.get("WorkingDir") and config.get("WorkingDir") != image_config.get("WorkingDir"):
        kwargs["working_dir"] = config["WorkingDir"]
    if config.get("User") and config.get("User") != image_config.get("User"):
        kwargs["user"] = config["User"]
    if config.get("StopSignal") and config.get("StopSignal") != image_config.get("StopSignal"):
        kwargs["stop_signal"] = config["StopSignal"]
    if config.get("StopTimeout") is not None:
        kwargs["stop_timeout"] = config["StopTimeout"]

    hostname = config.get("Hostname")
    network_mode = host_config.get("NetworkMode") or ""
    # A container sharing another container's network namespace always
    # inherits that container's hostname -- Docker rejects an explicit
    # hostname outright when network_mode is "container:X". In that mode,
    # Config.Hostname reports the *other* container's id/name, which never
    # matches this container's own id, so the plain not-the-default-id-prefix
    # check below would otherwise wrongly treat it as a custom hostname worth
    # setting explicitly (caught live: this made every container using
    # --net=container:X fail to recreate at all, unconditionally).
    shares_network_namespace = network_mode.startswith("container:")
    if hostname and not shares_network_namespace and not container.id.startswith(hostname):
        kwargs["hostname"] = hostname

    mounts, binds = _build_mounts(inspect.get("Mounts") or [])
    if mounts:
        kwargs["mounts"] = mounts
    if binds:
        kwargs["volumes"] = binds

    links = _build_links(host_config.get("Links") or [])
    if links:
        kwargs["links"] = links

    restart_policy = host_config.get("RestartPolicy") or {}
    if restart_policy.get("Name") and restart_policy["Name"] != "no":
        kwargs["restart_policy"] = restart_policy

    if host_config.get("CapAdd"):
        kwargs["cap_add"] = host_config["CapAdd"]
    if host_config.get("CapDrop"):
        kwargs["cap_drop"] = host_config["CapDrop"]
    if host_config.get("Privileged"):
        kwargs["privileged"] = True
    if host_config.get("VolumesFrom"):
        kwargs["volumes_from"] = host_config["VolumesFrom"]

    if host_config.get("Ulimits"):
        kwargs["ulimits"] = [Ulimit(**u) for u in host_config["Ulimits"]]
    if host_config.get("Sysctls"):
        kwargs["sysctls"] = host_config["Sysctls"]
    if host_config.get("Devices"):
        kwargs["devices"] = [
            f"{d['PathOnHost']}:{d['PathInContainer']}:{d['CgroupPermissions']}"
            for d in host_config["Devices"]
        ]
    if host_config.get("DeviceRequests"):
        kwargs["device_requests"] = [
            DeviceRequest(**dr) for dr in host_config["DeviceRequests"]
        ]
    if host_config.get("Dns"):
        kwargs["dns"] = host_config["Dns"]
    if host_config.get("DnsSearch"):
        kwargs["dns_search"] = host_config["DnsSearch"]
    if host_config.get("DnsOptions"):
        kwargs["dns_opt"] = host_config["DnsOptions"]
    if host_config.get("ExtraHosts"):
        # HostConfig reports "host:ip" strings; create() wants a {host: ip} dict.
        kwargs["extra_hosts"] = dict(h.split(":", 1) for h in host_config["ExtraHosts"])
    if host_config.get("Tmpfs"):
        kwargs["tmpfs"] = host_config["Tmpfs"]

    if host_config.get("Memory"):
        kwargs["mem_limit"] = host_config["Memory"]
    if host_config.get("MemoryReservation"):
        kwargs["mem_reservation"] = host_config["MemoryReservation"]
    # -1 means "use the daemon default" (the common, un-set case); any other
    # value (0-100) is a meaningful explicit --memory-swappiness.
    memory_swappiness = host_config.get("MemorySwappiness")
    if memory_swappiness is not None and memory_swappiness != -1:
        kwargs["mem_swappiness"] = memory_swappiness
    if host_config.get("NanoCpus"):
        kwargs["nano_cpus"] = host_config["NanoCpus"]
    if host_config.get("CpuShares"):
        kwargs["cpu_shares"] = host_config["CpuShares"]
    if host_config.get("CpusetCpus"):
        kwargs["cpuset_cpus"] = host_config["CpusetCpus"]
    if host_config.get("CpusetMems"):
        kwargs["cpuset_mems"] = host_config["CpusetMems"]
    if host_config.get("CpuQuota"):
        kwargs["cpu_quota"] = host_config["CpuQuota"]
    if host_config.get("CpuPeriod"):
        kwargs["cpu_period"] = host_config["CpuPeriod"]
    if host_config.get("BlkioWeight"):
        kwargs["blkio_weight"] = host_config["BlkioWeight"]
    if host_config.get("OomScoreAdj"):
        kwargs["oom_score_adj"] = host_config["OomScoreAdj"]
    if host_config.get("OomKillDisable"):
        kwargs["oom_kill_disable"] = True
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
    if host_config.get("UsernsMode"):
        kwargs["userns_mode"] = host_config["UsernsMode"]
    if host_config.get("UTSMode"):
        kwargs["uts_mode"] = host_config["UTSMode"]
    if host_config.get("CgroupParent"):
        kwargs["cgroup_parent"] = host_config["CgroupParent"]
    if host_config.get("Isolation"):
        kwargs["isolation"] = host_config["Isolation"]
    # "runc" is Runtime's own reported default when nothing custom was set.
    runtime = host_config.get("Runtime")
    if runtime and runtime != "runc":
        kwargs["runtime"] = runtime

    container_healthcheck = config.get("Healthcheck")
    if container_healthcheck == image_config.get("Healthcheck"):
        # Unmodified since the old image's own HEALTHCHECK -- leave unset so
        # the new image's (possibly different) HEALTHCHECK takes effect.
        container_healthcheck = None
    healthcheck = _build_healthcheck(container_healthcheck)
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


def _build_links(raw_links: list[str]) -> dict[str, str]:
    """"/other-name:/this-name/alias" -> {"other-name": "alias"}, for legacy
    `--link`. HostConfig.Links records each side's fully-resolved name at
    link-creation time, not raw user input -- the alias is just the last
    path segment of the target side. docker-py's `links` kwarg accepts this
    exact {name: alias} shape (docker.utils.normalize_links)."""
    links: dict[str, str] = {}
    for raw in raw_links:
        source, _, target = raw.partition(":")
        name = source.lstrip("/")
        alias = target.rsplit("/", 1)[-1]
        links[name] = alias
    return links


_SUPPORTED_MOUNT_TYPES = {"bind", "volume", "tmpfs"}


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

    A `--mount type=tmpfs` entry (distinct from the legacy `--tmpfs` flag,
    which populates `HostConfig.Tmpfs` directly and is handled separately in
    `build_create_kwargs`) has no `Source`/relabel concept at all -- it's
    carried over as its own `Mount(type="tmpfs")` entry. `TmpfsOptions`
    (size/mode) isn't reliably present on every Docker version's runtime
    `Mounts` summary, so it's read opportunistically rather than required.
    """
    mounts: list[Mount] = []
    binds: list[str] = []
    for m in raw_mounts:
        mount_type = m.get("Type", "volume")
        if mount_type not in _SUPPORTED_MOUNT_TYPES:
            # Anything else Docker might report here isn't carried over —
            # skip explicitly rather than relying on the resulting empty
            # source being harmlessly falsy past docker-py's own handling.
            continue
        if mount_type == "tmpfs":
            tmpfs_opts = m.get("TmpfsOptions") or {}
            tmpfs_mode = tmpfs_opts.get("Mode")
            mounts.append(
                Mount(
                    target=m["Destination"],
                    source=None,
                    type="tmpfs",
                    tmpfs_size=tmpfs_opts.get("SizeBytes"),
                    # Mount() requires an int here (raises otherwise) --
                    # guard against an unexpected shape rather than crash.
                    tmpfs_mode=tmpfs_mode if isinstance(tmpfs_mode, int) else None,
                )
            )
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
    if mode in _NO_EXTRA_NETWORKS_MODES or mode.startswith("container:"):
        return []
    networks = inspect.get("NetworkSettings", {}).get("Networks") or {}
    if mode in _DEFAULT_NETWORK_MODES and len(networks) <= 1:
        # A single default-bridge attachment (the common case) needs no
        # explicit handling here -- create() auto-attaches it. More than one
        # entry means the container was additionally attached to a custom
        # network after creation via `docker network connect`, which
        # NetworkMode itself never reflects (it only ever names the
        # *primary* network from create time) -- carry all of them over
        # explicitly in that case, same as a container created directly on a
        # custom network below (including the "bridge" entry itself, so the
        # disconnect-then-reconnect-everything-listed dance in
        # DockerPyClient.recreate() puts it back too).
        return []
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
