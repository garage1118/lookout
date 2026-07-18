# lookout

[![Docker Pulls](https://img.shields.io/docker/pulls/garage1118/lookout)](https://hub.docker.com/r/garage1118/lookout)
[![Docker Image Size](https://img.shields.io/docker/image-size/garage1118/lookout/latest)](https://hub.docker.com/r/garage1118/lookout)
[![Docker Image Version](https://img.shields.io/docker/v/garage1118/lookout?sort=semver)](https://hub.docker.com/r/garage1118/lookout)

A Python reimplementation of [Watchtower](https://github.com/containrrr/watchtower)'s core
functionality: poll running containers, compare against the registry's latest
image digest, and recreate containers that are stale.

## Documentation

- [docs/index.md](docs/index.md) — quick start (`docker run`/Compose) and full doc index
- [docs/arguments.md](docs/arguments.md) — every CLI flag and `LOOKOUT_*` env var
- [docs/container-selection.md](docs/container-selection.md) — include/exclude, monitor-only, no-pull
- [docs/linked-containers.md](docs/linked-containers.md) — dependency-ordered stop/start
- [docs/lifecycle-hooks.md](docs/lifecycle-hooks.md) — pre/post-update hooks
- [docs/private-registries.md](docs/private-registries.md) — registry authentication
- [docs/notifications.md](docs/notifications.md) — Apprise run-summary notifications
- [docs/limitations.md](docs/limitations.md) — what's intentionally not implemented in v1
- [ROADMAP.md](ROADMAP.md) — planned for a future release
- [CHANGELOG.md](CHANGELOG.md) — release history

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
