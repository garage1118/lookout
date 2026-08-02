# Container selection

By default, lookout monitors every running container except itself. Five rules can exclude a
container, and a container must pass all of them to be monitored:

0. **Self-exemption** — always applies. Not configurable. See below.
1. **Disable via label** — always applies.
2. **`--label-enable` scope** — off by default. When on, only explicitly-enabled containers
   qualify, unless the container is explicitly named in `--include` (see below).
3. **`--scope`** — off by default. See below.
4. **`--include`/`--exclude` by name** — exclude always wins.

## Self-exemption

lookout never targets its own container. This holds even if the container would otherwise match
every inclusion rule — no disable label, on an `--include` list, and so on. You cannot override
it. Stopping itself to recreate itself is inherently risky: if the recreate fails partway through,
nothing is left running to retry it.

lookout detects its own container id from `/proc/self/mountinfo`'s `/etc/hostname` bind-mount
source. Docker always sets that source to `/var/lib/docker/containers/<real-id>/hostname` on the
host. lookout deliberately avoids `$HOSTNAME` for this. `$HOSTNAME` reflects whatever `--hostname`
was set to, or left at Docker's default. Stack/Compose deployments often pin an explicit hostname
unrelated to the container's actual id. lookout falls back to `$HOSTNAME` only as a last resort,
when `/proc` is not available at all (for example, not running on Linux).

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

To monitor only containers that explicitly opt in, pass `--label-enable` (or set
`LOOKOUT_LABEL_ENABLE=true`) on lookout. Then set `io.lookout.enable=true` on each container you
want it to watch:

```bash
docker run -d --label io.lookout.enable=true someimage
```

With `--label-enable` set, lookout does not monitor a container that lacks the label, even though
the label's absence would otherwise default to "enabled." The one exception: naming that container
in `--include` bypasses the label-enable gate for it specifically.

This exception exists for containers that cannot practically carry a label at all — Portainer
stacks are the main case. Naming one explicitly in `--include` is a strong enough signal to widen
scope for that container, without turning `--label-enable` off for every other container.

This bypass only widens *scope*. An explicit `io.lookout.enable=false` disable (previous section)
and monitor-only/no-pull both still apply, regardless of how a container entered scope.

```bash
lookout --label-enable --include hard-to-label-container
```

## Scope (split a daemon between several instances)

`--scope`/`LOOKOUT_SCOPE` splits one Docker daemon's containers between several independent
lookout instances, each responsible for a different subset. Tag the containers one instance should
own with `io.lookout.scope`, and pass the matching value to that instance:

```bash
docker run -d --label io.lookout.scope=dev someimage
```

```bash
lookout --scope dev
```

An instance with `--scope` set only monitors containers whose `io.lookout.scope` label matches
that exact value — an unscoped container, or one tagged with a different scope, is left alone. An
instance with no `--scope` set does the opposite: it ignores any container that carries the scope
label at all, on the assumption that some other, scoped instance owns it, and monitors everything
else as usual. You do not need to opt into that behavior — it is the default the moment any
container anywhere is scope-labeled.

Naming a scoped container explicitly in `--include` bypasses the scope gate for it specifically,
the same way `--include` bypasses `--label-enable` scope. This is useful for a one-off check across
scopes without changing either instance's `--scope` setting.

This is how you run, for example, a fast-interval instance dedicated to one actively-developed
private registry alongside a normal-interval instance for everything else, without hand-maintaining
matching `--include`/`--exclude` lists across both — the scope label is the single source of truth
for which instance owns a container.

## Include / exclude by name

```bash
lookout --include web --include worker --exclude scratch-db
```

Unlike Watchtower, which takes container names as positional CLI arguments, lookout uses explicit
`--include`/`--exclude` flags (or `LOOKOUT_INCLUDE_NAMES`/`LOOKOUT_EXCLUDE_NAMES`, as comma-separated
strings). Exclude always wins: a name in both lists is excluded.

Avoid filtering out a container that shares its network namespace with a monitored one
(`--net=container:<name>`, see [Linked containers](linked-containers.md)). lookout only stops and
recreates containers it actually monitors. So it cannot cascade a filtered-out network-mode
dependent into the same-run recreate the way it would an in-scope one. That dependent's
`container:<name>` reference goes stale the next time the target is recreated. lookout logs a
warning when it detects this — a stale target with a filtered-out dependent — but the only fix is
to include the dependent too.

## Monitor only

Individual containers can be marked to be checked and reported on, but never actually
stopped/recreated. Use this for a container you want visibility into without automatic changes —
a database you would rather update by hand, for example:

```bash
docker run -d --label io.lookout.monitor-only=true someimage
```

This has the same effect as the global `--monitor-only`/`LOOKOUT_MONITOR_ONLY` flag, but scoped to
that one container. The global flag and the label combine with OR: if either is set, lookout
leaves the container alone. lookout has no "label takes precedence over the global flag" toggle.
Watchtower has one.

## No pull

Similarly, `io.lookout.no-pull=true` on a container means lookout recreates it from whatever image
is already cached locally, instead of pulling. Use this when something else already puts the new
image on the host. Two examples: a CI job that pulls it, or an image built directly on the Docker
host and never pushed to a registry at all. This applies to that one container, with the same
OR-combination against the global `--no-pull`/`LOOKOUT_NO_PULL` flag.
