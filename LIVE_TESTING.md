# lookout v1 live-testing checklist

Tracks which v1 features have been exercised against a real Docker daemon / real registry, as
opposed to only the unit/fixture test suite. This is the readiness gate for cutting a `1.0.0` tag
— see CLAUDE.md's "Testing conventions" section for why fixture/fake tests alone haven't caught
every real bug in this codebase.

## Confirmed live

- [x] Core recreate flow: stop -> create -> start (`docker/client.py`, `docker/recreate.py`) —
      caught the bridge-network-swap bug during original development
- [x] Bind mounts, named volumes, multiple custom networks with aliases, restart policy,
      healthchecks (`docker/recreate.py`) — captured as real inspect fixtures from live containers
- [x] Registry digest lookup against Docker Hub, GHCR, and a personal basic-auth-only registry
      (`registry/digest.py`) — caught GHCR's bogus probe-scope bug
- [x] RepoDigests-orphaning staleness fallback (`core/updater._is_stale`) — caught by rebuilding
      and repushing the same tag on the same host running lookout
- [x] Scheduler SIGTERM/SIGINT handling within ~1s (`scheduler.py`)
- [x] Self-container detection via `/proc/self/mountinfo` (`core/filter.py`) — caught the
      Portainer stale-`$HOSTNAME` bug
- [x] `--label-enable` filtering — confirmed 2026-07-13

## Not yet confirmed live

- [ ] Lifecycle hooks: pre-update / post-update (`core/lifecycle.py`) — also has no unit tests
- [ ] Notifications via Apprise (`notifications/notify.py`)
- [ ] Private-registry credential reading from `~/.docker/config.json` (`registry/auth.py`)
- [ ] Image cleanup after update, `--cleanup` (`core/updater._cleanup_images`) — documented as
      best-effort/unverified
- [ ] Dependency-ordered stop/start (`stop_order`, container links / `depends-on` label)
- [ ] `--monitor-only`
- [ ] `--no-pull`
- [ ] `--include` / `--exclude` name filtering

## New since the 2026-07-14 code review pass — need first-time live verification

- [ ] Rename-first `recreate()` (`docker/client.py`) — replaces the old remove-then-create flow;
      needs a real create-failure case exercised (a rejected `HostConfig` field, a vanished
      network) to prove the old container survives instead of being lost
- [ ] No-pull restart-loop guard (`core/updater.py`) — needs confirming alongside `--no-pull`
      itself
- [ ] Post-update hook errors no longer mark a successful update as failed (`core/updater.py`) —
      needs confirming alongside lifecycle hooks
- [ ] Explicit tmpfs-mount skip in recreate (`docker/recreate.py` `_build_mounts`) — confirm a
      container with a tmpfs mount recreates cleanly without it
- [ ] Ulimits/sysctls/devices/dns/extra_hosts/tmpfs carried over on recreate (`docker/recreate.py`)
      — needs a container actually using each of these to confirm the new create-kwargs are
      accepted by a live daemon
- [ ] `container:<id>` NetworkMode resolved to `container:<name>` at listing time
      (`docker/client.py` `_resolve_network_mode_container_ref`) — confirm against a container
      whose network mode is `container:<other-container>`, and confirm the target surviving its
      own recreation doesn't break this one
