# Container selection

By default, lookout monitors every running container except itself. There are four ways
containers get excluded, and they combine (a container must pass all of them to be monitored):

0. **Self-exemption** — always applies, not configurable. See below.
1. **Disable via label** — always applies.
2. **`--label-enable` scope** — off by default; when on, only explicitly-enabled containers qualify.
3. **`--include`/`--exclude` by name** — exclude always wins.

## Self-exemption

lookout never targets its own container, even if it would otherwise match every inclusion rule
(no disable label, on an `--include` list, etc.) — this cannot be overridden. Stopping itself to
recreate itself is inherently risky: if the recreate fails partway through, nothing would be left
running to retry it.

It detects its own container id via `/proc/self/mountinfo`'s `/etc/hostname` bind-mount source,
which Docker always sets to `/var/lib/docker/containers/<real-id>/hostname` on the host —
deliberately not `$HOSTNAME`, since that reflects whatever `--hostname` was set to (or left at
Docker's default), and stack/Compose deployments often pin an explicit hostname unrelated to the
container's actual id. `$HOSTNAME` is only used as a last-resort fallback if `/proc` isn't
available at all (e.g. not running on Linux).

## Disable via label

Set `io.lookout.enable` to `false` **on the container you want ignored** (not on lookout itself):

```bash
docker run -d --label io.lookout.enable=false someimage
```

```yaml
services:
  someimage:
    labels:
      - "io.lookout.enable=false"
```

## Label enable (opt-in scope)

If you'd rather only monitor containers that explicitly opt in, pass `--label-enable` (or set
`LOOKOUT_LABEL_ENABLE=true`) on lookout, and set `io.lookout.enable=true` on the containers you
want watched:

```bash
docker run -d --label io.lookout.enable=true someimage
```

With `--label-enable` set, a container *without* the label is not monitored, even though the
label's absence would otherwise default to "enabled."

## Include / exclude by name

```bash
lookout --include web --include worker --exclude scratch-db
```

Unlike Watchtower, which takes container names as positional CLI arguments, lookout uses explicit
`--include`/`--exclude` flags (or `LOOKOUT_INCLUDE_NAMES`/`LOOKOUT_EXCLUDE_NAMES`, as JSON arrays).
Exclude always wins: a name in both lists is excluded.

## Monitor only

Individual containers can be marked to be checked and reported on, but never actually
stopped/recreated:

```bash
docker run -d --label io.lookout.monitor-only=true someimage
```

This has the same effect as the global `--monitor-only`/`LOOKOUT_MONITOR_ONLY` flag, but scoped to
that one container. The global flag and the label combine with OR: if either is set, the container
is left alone. There's no "label takes precedence over the global flag" toggle (Watchtower has
one; lookout doesn't).

## No pull

Similarly, `io.lookout.no-pull=true` on a container means lookout will recreate it from whatever
image is already cached locally instead of pulling, scoped to that one container — same
OR-combination with the global `--no-pull`/`LOOKOUT_NO_PULL` flag.
