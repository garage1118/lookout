# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

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
