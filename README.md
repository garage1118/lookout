# lookout

[![Docker Pulls](https://img.shields.io/docker/pulls/garage1118/lookout)](https://hub.docker.com/r/garage1118/lookout)
[![Docker Image Size](https://img.shields.io/docker/image-size/garage1118/lookout/latest)](https://hub.docker.com/r/garage1118/lookout)
[![Docker Image Version](https://img.shields.io/docker/v/garage1118/lookout?sort=semver)](https://hub.docker.com/r/garage1118/lookout)

A Python reimplementation of [Watchtower](https://github.com/containrrr/watchtower)'s core
functionality: poll running containers, compare against the registry's latest
image digest, and recreate containers that are stale.

## Documentation

Full documentation: **https://garage1118.github.io/lookout/**

- [Quick start](https://garage1118.github.io/lookout/) — `docker run`/Compose and full doc index
- [Arguments](https://garage1118.github.io/lookout/arguments/) — every CLI flag and `LOOKOUT_*` env var
- [Container selection](https://garage1118.github.io/lookout/container-selection/) — include/exclude, monitor-only, no-pull
- [Linked containers](https://garage1118.github.io/lookout/linked-containers/) — dependency-ordered stop/start
- [Lifecycle hooks](https://garage1118.github.io/lookout/lifecycle-hooks/) — pre/post-update hooks
- [Private registries](https://garage1118.github.io/lookout/private-registries/) — registry authentication
- [Notifications](https://garage1118.github.io/lookout/notifications/) — Apprise run-summary notifications
- [Limitations](https://garage1118.github.io/lookout/limitations/) — what's intentionally not implemented in v1
- [ROADMAP.md](https://github.com/garage1118/lookout/blob/main/ROADMAP.md) — planned for a future release
- [CHANGELOG.md](https://github.com/garage1118/lookout/blob/main/CHANGELOG.md) — release history

## Development

```
uv sync
uv run pytest
uv run ruff check .
uv run mypy lookout
```

## Run

```
uv run lookout --run-once
```

Configuration is via `LOOKOUT_*` environment variables (see `lookout/config.py`)
or a `.env` file.
