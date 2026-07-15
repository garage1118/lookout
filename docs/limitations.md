# Limitations

lookout is a v1 functional reimplementation of Watchtower's core loop, not a feature-for-feature
port. Some things are intentionally out of scope; others are gaps discovered while building the
modules that touch them. Both are listed here so they're not mistaken for bugs.

## Out of scope for v1

- **Docker Swarm service updates.** lookout only targets a single Docker daemon's containers.
- **HTTP API / webhook-triggered updates.** Watchtower can run in a mode where updates are only
  triggered by an HTTP request instead of polling. Not implemented.
- **Multi-host / fleet management.** One lookout instance manages one Docker daemon.
- **Running multiple scoped instances** (Watchtower's `--scope` label, for running several
  independent Watchtowers against the same daemon). Not implemented.
- **A Prometheus metrics endpoint.** Not implemented.
- **`--health-check` mode** for use as a container `HEALTHCHECK` command. Not implemented.

## Scheduling

Only simple interval polling (`--interval`/`LOOKOUT_INTERVAL_SECONDS`) is implemented. `Settings`
has a `cron_schedule` field reserved for future cron-style scheduling, but nothing currently reads
or acts on it — setting it has no effect.

## Container recreation

`docker/recreate.py` translates a running container's config into create-kwargs for its
replacement.

`Cmd`, `Entrypoint`, `Env`, `Labels`, `WorkingDir`, `User`, `StopSignal`, and `Healthcheck` are
subtracted against the *old* image's own `Config` before being copied onto the replacement — a
value that only appears in the container's inspect because the old image baked it in as a default
(not because it was ever explicitly overridden) is left out, so the new image's own default takes
over instead of being permanently shadowed. `ExposedPorts` is not subtracted this way (an
old-image-only `EXPOSE` entry is copied over as harmless metadata, not a functional override) — see
`docker/recreate.py`'s module docstring.

`--gpus`/`DeviceRequests`, `--runtime`, `--dns-search`/`--dns-opt`, `--volumes-from`,
`--userns`/`--uts`, `--cgroup-parent`, `--isolation`, CPU pinning (`--cpuset-cpus`/
`--cpuset-mems`/`--cpu-quota`/`--cpu-period`), `--blkio-weight`, `--oom-score-adj`/
`--oom-kill-disable`, `--memory-reservation`/`--memory-swappiness`, and `--mount type=tmpfs`
(distinct from the legacy `--tmpfs` flag, which was already carried over) are all carried over on
recreate — previously silently dropped.

A container attached to an additional custom network after creation via `docker network connect`
(rather than at `docker run --network` time) keeps that attachment across a recreate even though
`HostConfig.NetworkMode` itself only ever names the *primary* network from create time and never
reflects later `connect()` calls — `docker/recreate.py`'s `_build_networks` looks at
`NetworkSettings.Networks` directly rather than trusting `NetworkMode` alone for this.

Non-bridge/custom `NetworkMode` values other than `container:<id>` (e.g. `host`, or an exotic
driver-specific mode) are passed through as `network_mode` but not validated against a live
daemon before the recreate call — deliberately left out of v1 since an invalid value already fails
safely (`recreate()` creates, network-attaches, *and starts* the replacement as one rollback-able
unit, restoring the old container's name if any of that fails) rather than losing anything;
pre-validating would only make the error message clearer, not change the outcome. Starting is
included in that rollback specifically because Docker doesn't validate a `--net=container:X`
target's existence until start()-time, not create()-time — a real, live-caught gap in an earlier
version of `recreate()`, which left starting to the caller and could permanently lose the old
container if the target vanished between create() and start(). `--net=container:<id>` references
are also resolved to `container:<name>` at listing time so they survive the target container
itself being recreated (which changes its id) — see
`DockerClient._resolve_network_mode_container_ref()`. Separately, a container sharing another
container's network namespace inherits that container's hostname; `recreate()` knows not to set an
explicit `hostname` in that case, since Docker rejects the combination outright (also a real,
live-caught bug — it affected every `--net=container:X` container unconditionally, not just an edge
case).

SELinux bind-mount relabeling (`:z`/`:Z`) is carried over, but not through the same mechanism as
other mounts: the modern Mount type used for everything else has no field for it at all (that flag
is a legacy `-v`/`Binds`-only concept), so a mount using it is instead carried over as a legacy
`Binds`-style string, coexisting in the same `create()` call alongside the modern `mounts` list for
everything else. See `_build_mounts()` in `docker/recreate.py`.

Separately, containers published with `-P` get their ephemeral host ports **pinned** on recreate:
the previously assigned host port is reused verbatim instead of a fresh one being chosen. This
isn't a lookout gap — `docker inspect` only ever records the concrete host port a container ended
up with, so there's no way after the fact to tell "chosen by `-P`" apart from "fixed via
`-p hostport:containerport`". Watchtower has the identical behavior for the identical reason.

Also not implemented: `--remove-volumes` (removing anonymous volumes on update),
`--include-stopped`/`--include-restarting`/`--revive-stopped` (lookout only ever considers
already-running containers), and `--rolling-restart` (containers are always processed as a full
stop-all/start-all batch per the dependency order, not one at a time).

## Registry authentication

Credential helpers (`credHelpers`/`credsStore` in `config.json`) are not supported — see
[Private registries](private-registries.md). Only the plain `auths` section is read.

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
