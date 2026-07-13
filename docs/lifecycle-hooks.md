# Lifecycle hooks

lookout can run a command **inside** a container immediately before it's stopped for an update,
and again immediately after the recreated container is started. Unlike Watchtower, there's no
global enable flag — a hook only runs if the corresponding label is present on the container, so
it's opt-in by construction.

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
executable. If the container isn't running, its hooks can't run — the update proceeds without them.

A non-zero exit code is logged as a warning but does **not** stop the update from proceeding.

## Not implemented

- **Pre-check / post-check hooks.** Watchtower runs these around the staleness *check* itself
  (not just the update). lookout reserves the label names
  (`io.lookout.lifecycle.pre-check` / `post-check`) but nothing currently invokes them.
- **Per-hook timeouts.** Watchtower kills a hook after 60s by default (configurable per-label).
  lookout's hook execution has no timeout — a hanging hook command blocks that container's update
  indefinitely.
