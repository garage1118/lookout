# Lifecycle hooks

lookout can run a command **inside** a container immediately before it stops the container for an
update, and again immediately after it starts the recreated container. Unlike Watchtower, lookout
has no global enable flag. A hook runs only if the corresponding label is present on the
container, so hooks are opt-in by construction.

| Type        | Docker container label          |
| ----------- | -------------------------------- |
| Pre-update  | `io.lookout.lifecycle.pre-update`  |
| Post-update | `io.lookout.lifecycle.post-update` |

```bash
docker run -d \
  --label io.lookout.lifecycle.pre-update="/dump-data.sh" \
  --label io.lookout.lifecycle.post-update="/restore-data.sh" \
  someimage
```

The command runs via `docker exec … /bin/sh -c "<command>"`, so the container needs a `sh`
executable. If the container is not running, its hooks cannot run, and the update proceeds
without them.

lookout logs a non-zero exit code as a warning, but does **not** stop the update because of it.

## Not implemented

- **Pre-check / post-check hooks.** Watchtower runs these around the staleness *check* itself
  (not just the update). lookout does not implement these at all.
- **Per-hook timeouts.** Watchtower kills a hook after 60s by default (configurable per-label).
  lookout's hook execution has no timeout — a hanging hook command blocks that container's update
  indefinitely.
