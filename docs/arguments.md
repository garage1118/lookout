# Arguments

Configuration is env-vars-first: every setting has a `LOOKOUT_*` environment variable, and most
have a corresponding CLI flag that overrides it. A flag that isn't passed never touches the
env-var-derived value — there's no way to "unset" an env var from the CLI, only to add an override
on top of it.

List-valued environment variables (`LOOKOUT_INCLUDE_NAMES`, `LOOKOUT_EXCLUDE_NAMES`,
`LOOKOUT_NOTIFICATION_URLS`) take a **comma-separated string**, not a JSON array:

```bash
LOOKOUT_INCLUDE_NAMES=web,worker
```

## Run once

Run a single check-and-update pass and exit, instead of running as a daemon.

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
             Default: (empty; monitor all containers)
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

Only monitor containers that have the `io.lookout.enable` label set to `true`.

```text
            Argument: --label-enable
Environment Variable: LOOKOUT_LABEL_ENABLE
                Type: Boolean
             Default: false
```

## Cleanup

Best-effort removal of an image after the container using it has been successfully recreated onto
a newer one. If the old image is still referenced by something else, removal is silently skipped
rather than erroring — see [Limitations](limitations.md).

```text
            Argument: --cleanup
Environment Variable: LOOKOUT_CLEANUP
                Type: Boolean
             Default: false
```

## Monitor only

Check for and report staleness, but never stop/recreate containers. Can also be set per-container
with the `io.lookout.monitor-only` label — the two combine with OR, not override: if either the
global flag or the label is set, that container is left alone.

```text
            Argument: --monitor-only
Environment Variable: LOOKOUT_MONITOR_ONLY
                Type: Boolean
             Default: false
```

## No pull

Never pull; recreate using whatever image is already present locally under that name/tag. Useful
if something else (e.g. a CI job) is responsible for pulling. Can also be set per-container with
the `io.lookout.no-pull` label (same OR-combination as monitor-only).

```text
            Argument: --no-pull
Environment Variable: LOOKOUT_NO_PULL
                Type: Boolean
             Default: false
```

## Docker host

Docker daemon to connect to. If omitted, the `docker` SDK's own defaults apply, which already
honor the standard `DOCKER_HOST`, `DOCKER_TLS_VERIFY`, and `DOCKER_CERT_PATH` environment
variables — so remote hosts and TLS work without a dedicated lookout flag for either.

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

Seconds to wait for a container to stop gracefully before Docker forces it. Env-var only; no CLI
flag currently exposes it.

```text
            Argument: N/A
Environment Variable: LOOKOUT_STOP_TIMEOUT_SECONDS
                Type: Integer
             Default: 10
```

## Notification URLs

[Apprise](https://github.com/caronc/apprise) service URLs to send the run summary to. Env-var
only; no CLI flag currently exposes it. See [Notifications](notifications.md).

```text
            Argument: N/A
Environment Variable: LOOKOUT_NOTIFICATION_URLS
                Type: Comma-separated string of Apprise service URLs
             Default: (empty; no notifications sent)
```

## Notify only on change

Skip sending a notification when nothing was updated, failed, or found stale — see
[Notifications](notifications.md).

```text
            Argument: --notify-only-on-change
Environment Variable: LOOKOUT_NOTIFY_ONLY_ON_CHANGE
                Type: Boolean
             Default: false
```

## Notify on startup

Send a one-time notification when lookout starts, separate from the per-run summary — see
[Notifications](notifications.md).

```text
            Argument: --notify-on-startup
Environment Variable: LOOKOUT_NOTIFY_ON_STARTUP
                Type: Boolean
             Default: false
```

## Registry host / username / password

A single fallback credential pair for one private registry, tried only for images on
`LOOKOUT_REGISTRY_HOST` when `config.json` has no matching entry (or there's no `config.json` at
all). `LOOKOUT_REGISTRY_HOST` is required for the other two to have any effect — without it the
credentials are never used, rather than being sent to every registry with no config.json entry
(which would break anonymous access to public images on unrelated registries). Env-var only —
putting a password on the CLI would leak it into shell history and `ps` output. See
[Private registries](private-registries.md).

```text
            Argument: N/A
Environment Variable: LOOKOUT_REGISTRY_HOST, LOOKOUT_REGISTRY_USERNAME, LOOKOUT_REGISTRY_PASSWORD
                Type: String
             Default: - (no fallback credentials)
```
