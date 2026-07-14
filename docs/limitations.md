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
replacement. The following are **not** carried over to the recreated container:

- SELinux bind-mount relabeling (`:z`/`:Z` mount options)
- Other non-bridge/custom `NetworkMode` values (e.g. `host`) are passed through as `network_mode`
  but not validated against a live daemon. `--net=container:<id>` specifically is resolved to
  `container:<name>` at listing time so the reference survives the target container itself being
  recreated (which changes its id) — see `DockerClient._resolve_network_mode_container_ref()`
- Resource limits (`Memory`, `NanoCpus`/`CpuShares`, `MemorySwap`, `PidsLimit`) — a recreated
  container silently loses its limits
- `LogConfig` (driver + options), `SecurityOpt`, `GroupAdd`, `ReadonlyRootfs`, `ShmSize`, `Init`,
  `StopSignal`/`StopTimeout`, `PidMode`/`IpcMode`
- Per-network static IPs (`IPAMConfig.IPv4Address`) and MAC addresses — aliases are kept, but a
  container with a pinned IP comes back with a dynamic one

Conversely, containers published with `-P` get their ephemeral host ports **pinned** on recreate
(the previously assigned host port is reused verbatim instead of a fresh one being chosen).

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
