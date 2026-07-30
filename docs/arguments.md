# Arguments

Configuration is env-vars-first. Every setting has a `LOOKOUT_*` environment variable, and most
also have a matching CLI flag that overrides it. A flag you do not pass never touches the
env-var-derived value. There is no way to "unset" an env var from the CLI — you can only add an
override on top of it.

List-valued environment variables (`LOOKOUT_INCLUDE_NAMES`, `LOOKOUT_EXCLUDE_NAMES`,
`LOOKOUT_NOTIFICATION_URLS`) take a **comma-separated string**, not a JSON array:

```bash
LOOKOUT_INCLUDE_NAMES=web,worker
```

## Run once

Run a single check-and-update pass and exit, instead of running as a daemon. Use this to drive
lookout from an external scheduler instead of its own polling loop. Examples: a cron job, a CI
pipeline, or a manual check right after pushing a new image.

```text
 Argument: --run-once
     Type: Boolean
  Default: false
```

## Poll interval

How often, in seconds, lookout checks containers for updates.

```text
            Argument: --interval
Environment Variable: LOOKOUT_INTERVAL_SECONDS
                Type: Integer
             Default: 300
```

## Include

Only monitor containers with this name. Repeatable.

```text
            Argument: --include (repeatable)
Environment Variable: LOOKOUT_INCLUDE_NAMES
                Type: Comma-separated string
             Default: (empty — monitors all containers)
```

## Exclude

Never monitor containers with this name. Repeatable. Exclude always wins over include and over the
enable label — see [Container selection](container-selection.md) for the full precedence order.

```text
            Argument: --exclude (repeatable)
Environment Variable: LOOKOUT_EXCLUDE_NAMES
                Type: Comma-separated string
             Default: (empty)
```

## Label enable

Only monitor containers that have the `io.lookout.enable` label set to `true`, instead of
monitoring every container by default. Use this on a host running containers you do not want
lookout to touch unless they opt in explicitly. See
[Container selection](container-selection.md) for how this combines with `--include`/`--exclude`.

```text
            Argument: --label-enable
Environment Variable: LOOKOUT_LABEL_ENABLE
                Type: Boolean
             Default: false
```

## Cleanup

Best-effort removal of the old image after lookout successfully recreates its container onto a
newer one, so superseded images do not pile up on disk. If something else still references the old
image, lookout silently skips the removal instead of raising an error. See
[Limitations](limitations.md).

```text
            Argument: --cleanup
Environment Variable: LOOKOUT_CLEANUP
                Type: Boolean
             Default: false
```

## Monitor only

Check for and report staleness, but never stop or recreate containers. Use this for containers you
want visibility into without automatic changes — a database you would rather update by hand, for
example. You can also set this per-container with the `io.lookout.monitor-only` label. The global
flag and the label combine with OR, not override: if either one is set, lookout leaves that
container alone.

```text
            Argument: --monitor-only
Environment Variable: LOOKOUT_MONITOR_ONLY
                Type: Boolean
             Default: false
```

## No pull

Never pull. Recreate using whatever image is already present locally under that name and tag. Use
this when something else already puts the new image on the host. Two examples: a CI job that pulls
it, or an image built directly on the Docker host and never pushed to a registry at all. You can
also set this per-container with the `io.lookout.no-pull` label (same OR-combination as
monitor-only).

```text
            Argument: --no-pull
Environment Variable: LOOKOUT_NO_PULL
                Type: Boolean
             Default: false
```

## Docker host

Docker daemon to connect to. Use this to point lookout at a different daemon than the default
without exporting `DOCKER_HOST` for the whole process. If omitted, the `docker` SDK's own defaults
apply. Those defaults already honor the standard `DOCKER_HOST`, `DOCKER_TLS_VERIFY`, and
`DOCKER_CERT_PATH` environment variables, so remote hosts and TLS work without a dedicated lookout
flag for either.

```text
            Argument: --docker-host
Environment Variable: LOOKOUT_DOCKER_HOST
                Type: String
             Default: - (SDK default: unix:///var/run/docker.sock, or DOCKER_HOST if set)
```

## Log level

```text
            Argument: --log-level
Environment Variable: LOOKOUT_LOG_LEVEL
     Possible values: any Python logging level name, e.g. DEBUG, INFO, WARNING, ERROR
             Default: INFO
```

## Stop timeout

Seconds to wait for a container to stop gracefully before Docker forces it with SIGKILL. Increase
this for containers that need longer to shut down cleanly, such as a database flushing its write
cache to disk. A SIGKILL mid-flush on such a container can cause data loss or corruption. This is
an env-var-only setting. No CLI flag currently exposes it.

```text
            Argument: N/A
Environment Variable: LOOKOUT_STOP_TIMEOUT_SECONDS
                Type: Integer
             Default: 10
```

## Notification URLs

[Apprise](https://github.com/caronc/apprise) service URLs to send the run summary to. This is an
env-var-only setting. No CLI flag currently exposes it. See [Notifications](notifications.md).

```text
            Argument: N/A
Environment Variable: LOOKOUT_NOTIFICATION_URLS
                Type: Comma-separated string of Apprise service URLs
             Default: (empty — sends no notifications)
```

## Notify only on change

Skip sending a notification when nothing was updated, failed, or found stale. This way, you are
not notified about every routine poll that finds nothing to do. See
[Notifications](notifications.md).

```text
            Argument: --notify-only-on-change
Environment Variable: LOOKOUT_NOTIFY_ONLY_ON_CHANGE
                Type: Boolean
             Default: false
```

## Notify on startup

Send a one-time notification when lookout starts, separate from the per-run summary. Use this to
confirm lookout came back up after a host reboot or its own update. You do not have to wait for
the next scheduled run summary — see [Notifications](notifications.md).

```text
            Argument: --notify-on-startup
Environment Variable: LOOKOUT_NOTIFY_ON_STARTUP
                Type: Boolean
             Default: false
```

## Registry host / username / password

A single fallback credential pair for one private registry. lookout tries it only for images on
`LOOKOUT_REGISTRY_HOST`, and only when `config.json` has no matching entry (or there is no
`config.json` at all). `LOOKOUT_REGISTRY_HOST` is required for the other two variables to have any
effect. Without it, lookout never uses the credentials. It does not send them to every registry that lacks
a config.json entry. That would break anonymous access to public images on unrelated registries. This is an env-var-only setting: putting a password on the CLI would leak it into shell
history and `ps` output. See [Private registries](private-registries.md).

```text
            Argument: N/A
Environment Variable: LOOKOUT_REGISTRY_HOST, LOOKOUT_REGISTRY_USERNAME, LOOKOUT_REGISTRY_PASSWORD
                Type: String
             Default: - (no fallback credentials)
```
