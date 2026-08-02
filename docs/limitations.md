# Limitations

lookout is a v1 functional reimplementation of Watchtower's core loop, not a feature-for-feature
port. Some things are intentionally out of scope. Others are gaps found while building the modules
that touch them. This page lists both, so they are not mistaken for bugs.

## Out of scope for v1

- **Docker Swarm service updates.** lookout only targets a single Docker daemon's containers.
  Service-managed containers get task-suffixed names (`<service>.<slot>.<task-id>`) that never
  match a plain `--include`. Even a container an operator does manage to select gets overwritten
  again immediately by Swarm's own reconciliation once lookout recreates it — a real Watchtower
  user lost weeks tracing a clean, error-free, zero-update poll back to exactly this problem. To
  avoid the same silent trap, lookout logs one explicit warning at startup when the daemon is a
  Swarm member (`DockerClient.is_swarm_active()`), instead of running quietly and updating nothing.
  It does not refuse to start: standalone, non-service containers on a Swarm-enabled daemon are
  still monitored normally.
- **HTTP API / webhook-triggered updates.** Watchtower can run in a mode where updates are only
  triggered by an HTTP request instead of polling. Not implemented.
- **Multi-host / fleet management.** One lookout instance manages one Docker daemon. Running
  several independent instances against the *same* daemon, each responsible for a different subset
  of containers, is supported via `--scope` — see
  [Container selection](container-selection.md#scope-split-a-daemon-between-several-instances).
- **A Prometheus metrics endpoint.** Not implemented.
- **`--health-check` mode** for use as a container `HEALTHCHECK` command. Not implemented — see
  the Roadmap doc in the repo root.

## Scheduling

Only simple interval polling (`--interval`/`LOOKOUT_INTERVAL_SECONDS`) is implemented. Cron-style
scheduling is not. See `scheduler.py`'s `run_forever()`, which is written so a cron-based scheduler
can replace its sleep loop later without touching the update logic itself. See the Roadmap doc in
the repo root.

## Container recreation

`docker/recreate.py` translates a running container's config into create-kwargs for its
replacement.

lookout subtracts `Cmd`, `Entrypoint`, `Env`, `Labels`, `WorkingDir`, `User`, `StopSignal`, and
`Healthcheck` against the *old* image's own `Config` before copying them onto the replacement. This
leaves out any value that appears in the container's inspect only because the old image baked it
in as a default, not because someone explicitly overrode it — so the new image's own default takes
over instead of staying permanently shadowed. `ExposedPorts` is not subtracted this way: an
old-image-only `EXPOSE` entry is copied over as harmless metadata, not a functional override. See
`docker/recreate.py`'s module docstring.

lookout carries all of the following over on recreate, having previously dropped them silently:
`--gpus`/`DeviceRequests`, `--runtime`, `--dns-search`/`--dns-opt`, `--volumes-from`,
`--userns`/`--uts`, `--cgroup-parent`, `--isolation`, CPU pinning (`--cpuset-cpus`/
`--cpuset-mems`/`--cpu-quota`/`--cpu-period`), `--blkio-weight`, `--oom-score-adj`/
`--oom-kill-disable`, `--memory-reservation`/`--memory-swappiness`, and `--mount type=tmpfs`
(distinct from the legacy `--tmpfs` flag, which lookout already carried over).

A container attached to an additional custom network after creation, via `docker network connect`
rather than at `docker run --network` time, keeps that attachment across a recreate.
`HostConfig.NetworkMode` itself only ever names the *primary* network from create time, and never
reflects later `connect()` calls, so `docker/recreate.py`'s `_build_networks` looks at
`NetworkSettings.Networks` directly instead of trusting `NetworkMode` alone.

lookout attaches every target network directly at `create()` time, each with its own full config
(aliases, static IP, MAC). This keeps `HostConfig.NetworkMode` accurate after a recreate too, not
just `NetworkSettings.Networks`. For a container on more than one network, `NetworkMode` names
whichever network happens to be first in `_build_networks()`'s output. Docker has no real concept
of "primary" beyond that field once a container has multiple attachments, so this choice is
arbitrary but harmless.

lookout passes non-bridge/custom `NetworkMode` values other than `container:<id>` (for example
`host`, or an exotic driver-specific mode) through as `network_mode`, but does not validate them
against a live daemon before the recreate call. This is a deliberate v1 gap: an invalid value
already fails safely, because `recreate()` creates, network-attaches, and starts the replacement
as one rollback-able unit, and restores the old container's name if any step fails.
Pre-validating would only make the error message clearer — it would not change the outcome.

Starting is part of that rollback specifically because Docker does not validate a
`--net=container:X` target's existence until start()-time, not create()-time. This was a real,
live-caught gap in an earlier version of `recreate()`, which left starting to the caller and could
permanently lose the old container if the target vanished between create() and start().

`--net=container:<id>` references are also resolved to `container:<name>` at listing time, so they
survive the target container itself being recreated, which changes its id. See
`DockerClient._resolve_network_mode_container_ref()`.

Separately, a container that shares another container's network namespace inherits that
container's hostname. `recreate()` knows not to set an explicit `hostname` in that case, because
Docker rejects the combination outright. This was also a real, live-caught bug — it affected every
`--net=container:X` container unconditionally, not just an edge case.

lookout carries SELinux bind-mount relabeling (`:z`/`:Z`) over, but not through the same mechanism
as other mounts. The modern Mount type used for everything else has no field for it at all — that
flag is a legacy `-v`/`Binds`-only concept. So a mount using it is instead carried over as a legacy
`Binds`-style string, alongside the modern `mounts` list for everything else in the same
`create()` call. See `_build_mounts()` in `docker/recreate.py`.

Separately, lookout **pins** the ephemeral host ports of containers published with `-P` on
recreate: it reuses the previously assigned host port verbatim, instead of choosing a fresh one.
This is a deliberate choice, not an inherent limitation. `HostConfig.PublishAllPorts` does record
whether `-P` was used, so lookout could pick a fresh ephemeral port each time instead. Pinning is
the more useful default for an auto-updater, though: anything that depends on the port staying the
same — a reverse-proxy config, a firewall rule, a bookmark — would otherwise break on every update
instead of staying stable across them. Watchtower has the identical behavior, for the identical
reason.

Also not implemented: `--remove-volumes` (removing anonymous volumes on update),
`--include-stopped`/`--include-restarting`/`--revive-stopped` (lookout only ever considers
already-running containers), and `--rolling-restart` (containers are always processed as a full
stop-all/start-all batch per the dependency order, not one at a time).

## Registry authentication

lookout does not support credential helpers (`credHelpers`/`credsStore` in `config.json`) — see
[Private registries](private-registries.md). It reads only the plain `auths` section.

The digest lookup's TLS trust is also independent of the Docker daemon's. A self-signed or
private-CA registry needs its CA trusted inside lookout's own container specifically
(`SSL_CERT_FILE`/`SSL_CERT_DIR`), not through the daemon's `/etc/docker/certs.d`. See
[Private registries](private-registries.md#tls-self-signed-or-private-ca-registries).

## Lifecycle hooks

Pre-check/post-check hooks (run around the staleness check itself, not just the update) are not
implemented — only pre-update/post-update. Hook commands have no execution timeout. See
[Lifecycle hooks](lifecycle-hooks.md).

## Notifications

No message templating and no notification log-level filtering — see
[Notifications](notifications.md).

## Labels

lookout uses its own `io.lookout.*` label prefix rather than Watchtower's
`com.centurylinklabs.watchtower.*` — it does not aim for label compatibility with Watchtower.
