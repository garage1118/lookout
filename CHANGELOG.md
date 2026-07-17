# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed

- A container lookout successfully updated could become permanently stuck skipped with
  `"no tagged image name"` on every later poll — the replacement was being created from the
  resolved image id instead of its tag, which left `Config.Image` looking untagged from then on.
- `--include` now works for containers that can't practically be labeled (e.g. Portainer stacks),
  even when `--label-enable` scope is on — an explicitly named `--include` container bypasses the
  label-enable gate specifically, without affecting an explicit disable or any other container's
  scope.
- Recreating a container on one or more custom networks (including macvlan) no longer leaves
  `HostConfig.NetworkMode` stale at `bridge`, and no longer drops a pinned MAC address on any
  network beyond the first — every target network is now attached directly at container-create
  time instead of via a create-then-swap step. The stale `NetworkMode` was more than cosmetic: it
  could cause a subsequent Portainer "Duplicate/Edit" of the same container to silently redeploy
  it onto the wrong network.

### Added

- Lookout now detects when the Docker daemon is a Swarm member and logs a warning at startup
  instead of silently running and updating nothing — a real failure mode reported against
  Watchtower under the same conditions.

### Documentation

- Documented TLS trust for self-signed/private-CA registries, a workaround for GCR/ECR
  credential-helper-only auth, registry propagation delay, and a few Apprise notification schemes
  worth calling out explicitly (Pushover, Bark, MQTT, generic JSON webhook, syslog).

## [1.0.0] - 2026-07-15

First stable release.

- Poll running containers and recreate them when a newer image digest is available in the
  registry, preserving runtime config (mounts, networks, environment, restart policy,
  healthcheck, etc.)
- Container selection via name include/exclude and `io.lookout.enable`/`.monitor-only`/`.no-pull`
  labels
- Dependency-ordered stop/start for linked containers (`io.lookout.depends-on`, legacy links)
- Pre/post-update lifecycle hooks via `docker exec`
- Private registry authentication via `~/.docker/config.json`
- Run-summary notifications through Apprise
- Docker Hub images for `linux/amd64` and `linux/arm64`

[1.0.0]: https://github.com/garage1118/lookout/releases/tag/v1.0.0
