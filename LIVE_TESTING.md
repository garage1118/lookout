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
- [x] SELinux bind-mount/volume relabeling (`:z`/`:Z`) carried over via legacy `Binds` strings
      alongside the modern `mounts` list (`docker/recreate.py` `_build_mounts`,
      `docker/client.py` `DockerPyClient._create`) — confirmed live 2026-07-14 on a real
      SELinux-enforcing RHEL 9 host (Docker CE 29.6.1 with `selinux-enabled: true` explicitly set;
      it's *not* on by default even on an SELinux-enforcing host — Docker CE only enables container
      SELinux separation when told to). Verified against a real container with a `:z` shared bind
      (read-write), a `:Z` private bind (read-only), a plain unlabeled bind (still correctly denied
      by SELinux both before and after recreate), and a `:z` named volume — all four preserved
      correct read/write behavior across a real stop/recreate/start cycle. This also caught a real
      bug in `docker-py` itself: its high-level `containers.create(volumes=[...])` independently
      derives `Config.Volumes` from the same bind strings via a helper
      (`_host_volume_from_bind`) that only understands a plain `ro`/`rw` mode suffix — given a
      compound mode like `rw,z` it fell through to using the raw `dest:mode` tail as a volume
      destination, silently creating garbage anonymous volumes alongside the correct bind (the
      `HostConfig.Binds` entry itself was fine; only the `Config.Volumes` side effect was
      wrong). Worked around by building the `HostConfig` via the low-level API instead
      (`DockerPyClient._create()`), which passes bind strings straight through with no such
      parsing. Real fixture captured: `tests/fixtures/inspect/selinux-relabel.json`.
- [x] Static per-network IPv4/IPv6 address and MAC address carried over on recreate
      (`docker/recreate.py` `_build_networks`, `docker/client.py` `recreate`) — confirmed live
      2026-07-14: a container on a custom network (with an IPv6 subnet) started with `--ip`,
      `--ip6`, `--mac-address`, and a network alias survived a real stop/recreate/start cycle with
      all four preserved exactly. No bugs found this time — unlike the SELinux item above,
      `docker-py`'s `Network.connect()` does correctly accept and apply the forwarded `mac_address`
      kwarg (previously only verified by reading its source, not exercised against a real daemon).
      Real fixture captured: `tests/fixtures/inspect/static-ip-mac.json`.
- [x] Rename-first `recreate()` survives a real failure (`docker/client.py`) — confirmed live
      2026-07-14, in two parts:
      - **create()-time rejection**: caught a real, separate, previously-undiscovered bug in the
        process — `build_create_kwargs()` treated a `--net=container:X` container's *inherited*
        hostname (Docker reports `Config.Hostname` as the network-sharing target's id/name, which
        never matches this container's own id) as a custom hostname worth setting explicitly.
        Docker rejects an explicit `hostname` outright whenever `network_mode` is `container:X`, so
        this made *every* such container fail to recreate, unconditionally — not an edge case.
        While this bug was still in place, the rename-first recovery worked exactly as documented:
        `create()` failed, the old container was renamed back to its original name, no orphaned
        container was left behind. Fixed in `build_create_kwargs()` (skip `hostname` when
        `network_mode` starts with `container:`) and confirmed live that recreate then succeeds.
      - **start()-time rejection — a more serious gap found in the same session**: for
        `--net=container:X` specifically, Docker's `create()` succeeds even when `X` no longer
        exists at all; the namespace-join is only validated at `start()`. The original
        `recreate()` left starting to the caller, so by the time `start()` failed, the *old*
        container had already been removed (create() had "succeeded") — a permanent, unrecoverable
        loss, not a retryable failure like every other case this safety net covers. Confirmed live
        by removing a `--net=container:X` target container entirely, then recreating the dependent
        container: `create()` succeeded, and only `docker start` surfaced
        `"joining network namespace of container: No such container"`. Fixed by moving `start()`
        (and the network-reconnect step) inside `recreate()`'s own try/except, so a start()
        failure now rolls back exactly like a create()-time one (removes the half-created
        container, renames the old one back). Confirmed live after the fix: same vanished-target
        scenario now correctly restores the old container with zero orphaned state.
        `core/updater.py` no longer calls `start()` separately after `recreate()` — the contract
        changed to "recreate() returns an already-running container."
- [x] `--no-pull` and its restart-loop guard (`core/updater.py`) — confirmed live 2026-07-14 against
      a real `DockerPyClient` and the real `core.updater.run()` orchestration (registry lookup
      itself was stubbed to a fixed bogus digest, since registry lookup correctness was already
      confirmed live separately and this test is specifically about the no-pull guard, which only
      depends on `docker_client` state, not on which digest a real registry happens to report right
      now). Two scenarios: (1) container stale per the (stubbed) registry digest, `--no-pull` set,
      no external change to the local image — correctly restarted in place: same container id, same
      image, still running, counted in `stale` but *not* `updated`. (2) same setup, but the local
      image was retagged out from under the container by something other than lookout (simulated
      via `docker tag`, standing in for an external `docker pull`/build) — correctly detected the
      mismatch and recreated: new container id, running the newly-tagged image, counted in
      `updated`. No bugs found — the guard added in the original code review pass behaves exactly as
      documented in both directions.
- [x] Notifications via Apprise (`notifications/notify.py`) — confirmed live 2026-07-14. Telegram
      was the original target (a real bot via `@BotFather`), but `api.telegram.org` turned out to be
      network-blocked from this host (TCP connects, TLS handshake gets reset — confirmed general
      internet egress works fine via a Docker Hub request, so this is specific to Telegram, not a
      general network issue). Fell back to a local HTTP listener and Apprise's generic `json://`
      webhook scheme instead, which still exercises the real risk (URL parsing, payload
      construction, real network delivery — not a mocked Apprise object). Confirmed: `send_startup()`
      delivers the right title/body; `send()` delivers a real `Session.summary()` render matching
      updated/failed/skipped containers; a malformed URL mixed in with a valid one logs a warning
      without crashing and doesn't block delivery to the valid one; `only_on_change=True` correctly
      sends zero requests for a no-activity session. No bugs found. Doesn't cover any
      service-specific payload quirks (Slack/Discord/Telegram's own expected shape) since the
      receiver was generic JSON, not a real chat service — worth a real Telegram/Slack run from a
      network that isn't blocking it, if that matters before 1.0.
- [x] Lifecycle hooks: pre-update / post-update, including post-update-error handling
      (`core/lifecycle.py`, `core/updater.py`) — confirmed live 2026-07-14 against a real
      `DockerPyClient` and the real `core.updater.run()` orchestration, in three parts:
      - **Happy path**: a container with both hooks set to `touch` a marker file on a bind-mounted
        host directory (SELinux-relabeled `container_file_t`, or the write is denied — same
        finding as the earlier SELinux item). Confirmed `pre-update` runs on the *old* container
        and `post-update` runs on the *new* one, by naming each marker after the container id that
        created it.
      - **Hook exits non-zero**: both hooks set to `exit 1` — confirmed a warning is logged for
        each (`lifecycle hook ... failed on ... (exit 1)`), and the update still proceeds normally
        (stop, recreate, start all happen; counted in `updated`, not `failed`) — `_run_hook` never
        raises for this case at all, so `updater.py`'s outer catch is never even involved here.
      - **Hook genuinely errors (not just a non-zero exit)**: removed a container out from under
        `lifecycle.post_update()` to force a real `exec_run()` failure — confirmed the real
        `DockerPyClient.exec_run()` raises `docker.errors.NotFound` in this case (a genuine
        exception, distinct from the exit-non-zero path above), validating the exact scenario
        `tests/test_updater.py`'s `FakeDockerClient.exec_fail` simulates. `updater.py`'s own
        catch-and-continue around that exception was already covered by
        `test_run_counts_update_as_successful_despite_post_update_hook_error`, which passes — this
        live check closes the gap of whether the real client can actually raise there at all,
        which the fake could only assume.

## Not yet confirmed live

- [ ] Private-registry credential reading from `~/.docker/config.json` (`registry/auth.py`)
- [ ] Image cleanup after update, `--cleanup` (`core/updater._cleanup_images`) — documented as
      best-effort/unverified
- [ ] Dependency-ordered stop/start (`stop_order`, container links / `depends-on` label)
- [ ] `--monitor-only`
- [ ] `--include` / `--exclude` name filtering

## New since the 2026-07-14 code review pass — need first-time live verification

- [ ] Explicit tmpfs-mount skip in recreate (`docker/recreate.py` `_build_mounts`) — confirm a
      container with a tmpfs mount recreates cleanly without it
- [ ] Ulimits/sysctls/devices/dns/extra_hosts/tmpfs carried over on recreate (`docker/recreate.py`)
      — needs a container actually using each of these to confirm the new create-kwargs are
      accepted by a live daemon
- [ ] `container:<id>` NetworkMode resolved to `container:<name>` at listing time
      (`docker/client.py` `_resolve_network_mode_container_ref`) — confirm against a container
      whose network mode is `container:<other-container>`, and confirm the target surviving its
      own recreation doesn't break this one
- [ ] Resource limits (`mem_limit`, `nano_cpus`, `cpu_shares`, `memswap_limit`, `pids_limit`)
      carried over on recreate (`docker/recreate.py`) — confirm against a container started with
      `--memory`/`--cpus`/`--pids-limit` set
- [ ] `log_config`/`security_opt`/`group_add`/`read_only`/`shm_size`/`init`/`stop_signal`/
      `stop_timeout`/`pid_mode`/`ipc_mode` carried over on recreate (`docker/recreate.py`) —
      confirm against a container started with `--log-opt`, `--security-opt`, `--read-only`,
      `--init`, `--stop-signal`, `--pid=host`, and `--ipc=shareable` set
