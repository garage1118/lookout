# lookout

lookout monitors your running Docker containers and watches for changes to the images those
containers were started from. If lookout detects that a container's image has a newer digest
available in the registry, it pulls the new image and recreates the container with the same
runtime configuration (mounts, networks, environment, restart policy, healthcheck, etc.) it had
before.

It's a Python reimplementation of [Watchtower](https://github.com/containrrr/watchtower)'s core
update loop — see the [Limitations](limitations.md) page for what's deliberately not included in v1.

## Quick start

lookout needs the Docker socket to see and manage containers:

```bash
docker run -d \
  --name lookout \
  -v /var/run/docker.sock:/var/run/docker.sock \
  garage1118/lookout:latest
```

Or with Compose:

```yaml
services:
  lookout:
    image: garage1118/lookout:latest
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
```

By default lookout monitors every running container and polls every 5 minutes
(`LOOKOUT_INTERVAL_SECONDS`, default `300`). Use `--include`/`--exclude` or labels to narrow that
down — see [Container selection](container-selection.md).

To run a single check-and-update pass instead of running as a daemon:

```bash
docker run --rm \
  -v /var/run/docker.sock:/var/run/docker.sock \
  garage1118/lookout:latest --run-once --log-level DEBUG
```

To build from source instead (e.g. for development):

```bash
docker build -t lookout .
```

## Documentation

- [Arguments](arguments.md) — every CLI flag and `LOOKOUT_*` environment variable
- [Container selection](container-selection.md) — include/exclude by name or label, monitor-only, no-pull
- [Linked containers](linked-containers.md) — dependency-ordered stop/start
- [Lifecycle hooks](lifecycle-hooks.md) — run commands inside containers around an update
- [Private registries](private-registries.md) — registry authentication
- [Notifications](notifications.md) — run-summary notifications via Apprise
- [Limitations](limitations.md) — what's intentionally not implemented in v1
- [Roadmap](../ROADMAP.md) — planned for a future release
