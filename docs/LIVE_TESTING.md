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

- [x] `--include` / `--exclude` name filtering (`core/filter.apply`) — confirmed live 2026-07-14
      against three real containers (`web`, `api`, `worker`): `--include web,api` left `worker`
      completely untouched (uptime unchanged) while `web`/`api` were processed; `--exclude worker`
      produced the identical result from the other direction. No bugs found.
- [x] `--monitor-only` (`core/updater.run`) — confirmed live 2026-07-14: a genuinely stale
      container was reported in `stale` but its container id and `State.StartedAt` were byte-for-byte
      unchanged after the run (no stop/recreate/restart call at all, not even a restart-in-place).
      No bugs found.
- [x] Dependency-ordered stop/start via the `depends-on` label (`stop_order`,
      `core/updater.run`) — confirmed live 2026-07-14 against two real containers (`db`, with `app`
      labelled `io.lookout.depends-on=db`), captured via real `docker events` during the run:
      `die app` fired, then (after the full stop timeout) `die db` — dependent stops first, exactly
      matching `stop_order()`. `start db` then `start app` followed — dependency starts first,
      exactly matching `reversed(stop_order())`. No bugs found.
- [x] Explicit tmpfs-mount skip in recreate (`docker/recreate.py` `_build_mounts`) — confirmed live
      2026-07-14. Note this needed disambiguating two distinct Docker features that both get called
      "tmpfs": legacy `--tmpfs /path:opts` (populates `HostConfig.Tmpfs` directly, no entry in the
      `Mounts` array) versus modern `--mount type=tmpfs,...` (populates the `Mounts` array with
      `Type: "tmpfs"`). A container with both was recreated: the legacy `--tmpfs` mount was
      correctly carried over (via the separate `host_config.get("Tmpfs")` handling added in the
      same pass, not `_build_mounts`), while the modern `--mount type=tmpfs` entry was correctly
      dropped — `_build_mounts`'s `_SUPPORTED_MOUNT_TYPES` skip is specifically about the latter.
      No bugs found once the two were tested separately.
- [x] Ulimits/sysctls/devices/dns/extra_hosts carried over on recreate (`docker/recreate.py`) —
      confirmed live 2026-07-14 against a real container started with `--ulimit nofile=2048:4096`,
      `--sysctl net.ipv4.ip_unprivileged_port_start=1023`, `--dns 9.9.9.9`,
      `--add-host myhost:10.1.2.3`, and `--device /dev/null:/dev/xnull` — all five survived a real
      recreate onto a new image unchanged. No bugs found.
- [x] Resource limits (`mem_limit`, `nano_cpus`, `memswap_limit`, `pids_limit`) carried over on
      recreate (`docker/recreate.py`) — confirmed live 2026-07-14 against a container started with
      `--memory 128m --memory-swap 256m --cpus 0.5 --pids-limit 128`; all four survived a real
      recreate unchanged. No bugs found. (`cpu_shares` specifically wasn't separately exercised —
      same plain-int passthrough pattern as the others, and `--cpus`/`nano_cpus` is the modern
      equivalent most real setups would actually use.)
- [x] `log_config`/`security_opt`/`group_add`/`read_only`/`init`/`stop_signal`/`stop_timeout`/
      `pid_mode`/`ipc_mode` carried over on recreate (`docker/recreate.py`, `docker/client.py`) —
      confirmed live 2026-07-14 against a container started with `--log-opt`,
      `--security-opt no-new-privileges:true`, `--group-add 1001`, `--read-only`, `--init`,
      `--stop-signal SIGUSR1`, `--stop-timeout 25`, `--pid host`, `--ipc shareable`. **Caught a
      real bug**: `stop_timeout` is accepted by the low-level `create_container()` API but is
      entirely unsupported by docker-py's high-level `containers.create()`/`.run()` (not in either
      of `RUN_CREATE_KWARGS`/`RUN_HOST_CONFIG_KWARGS` — confirmed by reading docker-py's own
      source), so any container recreated with a `--stop-timeout` in its original config raised a
      bare `TypeError: run() got an unexpected keyword argument 'stop_timeout'` before ever
      reaching the daemon — a 100%-reproducible failure, not an edge case, for any such container.
      The `recreate()` rollback correctly renamed the old container back with zero data loss, but
      it was left stopped (not restarted) — see the open item below. Fixed in
      `DockerPyClient._create()` by extending the existing low-level-API routing (previously only
      taken for the SELinux legacy-`Binds` case) to also trigger whenever `stop_timeout` is
      present; confirmed live after the fix that recreate succeeds and every one of the nine
      fields above survives correctly onto the new container.
- [x] Private-registry credential reading from `~/.docker/config.json` (`registry/auth.py`) —
      confirmed live 2026-07-14 with a real read-only manifest HEAD against
      `registry.3digital.com` (the same registry/scenario as the original `401` bug this project
      exists to avoid): `resolve_auth()` correctly read real Basic-auth credentials out of the
      host's actual `config.json`, and `RegistryClient.get_latest_digest()` got back a genuine
      `200` and digest instead of `401`. No bugs found. (Note: this check briefly printed the
      resolved password to a terminal in this session — that credential was flagged to the
      operator as compromised and should be rotated; not a code issue, a testing-hygiene mistake.)
- [x] Image cleanup after update, `--cleanup` (`core/updater._cleanup_images`) — confirmed live
      2026-07-14/15, both directions. First attempt used a **locally `docker build`-produced**
      image for the "before"/"after" pair, and found that on this host a locally-built image with
      only a container referencing it (no tag left after the retag) becomes fully untracked by
      Docker within the same moment the tag moves away (`docker image inspect` returns `404`
      immediately, even while a running container still references it) — apparently specific to
      how this host's BuildKit/containerd setup handles locally-built images, not something
      lookout's cleanup code can do anything about. `remove_image()` correctly hit that `404` and
      `_cleanup_images` logged `"skipping cleanup of ... (still in use?)"` and moved on without
      crashing the run — confirms the best-effort contract holds up even against an unexpected
      `404`, not just the documented "409 in use" case. That test wasn't representative of real
      usage, though: lookout only ever deals with **pulled** images, not locally-built ones.
      Redone with a real pulled image (`alpine:3.19` → `alpine:3.20` under a shared tag, the old
      tag fully removed so only the container references it, matching a real registry-driven
      update exactly): the old image stayed reachable after the retag (unlike the locally-built
      case), and after a real recreate, `_cleanup_images`'s `DELETE /images/<old-id>` call
      succeeded (`200`) — confirmed via `docker image inspect` that the old image was genuinely
      gone afterward, new container running the updated image throughout. Both the best-effort
      failure path and the real happy-path removal are now confirmed live.

## Known gap found during this session, fixed

- [x] `container:<id>` NetworkMode resolution (`docker/client.py`
      `_resolve_network_mode_container_ref`) only survived the target being recreated **within
      the same poll** as the dependent. Confirmed live 2026-07-14 with two containers
      (`netns-target`, `netns-dep` on `--network container:netns-target`): resolving id→name at
      `list_containers()` time works correctly, and a same-run recreate of both together works
      too. But Docker itself always re-resolves and re-stores a `container:<name>` reference as a
      concrete id at `create()` time — it never persists the name — so if the target got recreated
      in one poll (new id) while the dependent wasn't touched until a *later* poll, the
      dependent's `HostConfig.NetworkMode` (unchanged since Docker doesn't update it on a running
      container) still pointed at the target's now-**dead** id, permanently: reproduced by
      recreating `netns-target` alone, then `netns-dep` alone in the next run — `create()`
      succeeded but `start()` failed with `"joining network namespace of container: No such
      container: <dead id>"`, and every subsequent poll would fail identically forever, since
      nothing in Docker retains the old-id→new-id relationship once the old id is gone.
      **Fixed 2026-07-15** by making network-mode sharing a first-class dependency, same tier as
      the `depends-on` label: `Container.network_mode_target()` (new) feeds into `links()`, so
      `stop_order()` now sequences a network-mode dependent after its target automatically, no
      label needed; and `core/updater.run()` gained `_cascade_network_mode_dependents()`, which
      forces a *real* recreate (not the same-image restart-in-place shortcut) for any container
      whose `network_mode_target()` is stale this run, even though its own image is unchanged —
      this is what actually closes the gap, since it guarantees the dependent is always recreated
      in the *same* poll as its target, while the target's old id (and thus the id→name
      resolution) still exists. Covered by a new unit test
      (`test_run_cascades_recreate_to_network_mode_dependents`) and confirmed live 2026-07-15: two
      fresh containers (`cascade-target`, `cascade-dep`, the latter on an untouched `busybox:latest`
      so it's provably not independently stale), only `cascade-target`'s image rebuilt, single
      `run()` with both included — both recreated, zero failures, and `cascade-dep`'s new
      `NetworkMode` correctly points at `cascade-target`'s *new* id. **Residual caveat**: this only
      helps when both containers are in the same run's filtered target set — an operator using
      `--include`/`--exclude` asymmetrically (monitoring the target but explicitly excluding its
      network-mode dependent, or vice versa) can still hit the original gap, since cascading only
      operates over containers that already passed `core/filter.apply()`. Not expected to matter
      for the default (monitor-everything) configuration most real setups use.
- [x] Related, more general issue (not specific to the above): whenever `recreate()`'s rollback
      fired for *any* reason, the old container was correctly renamed back but left **stopped**,
      not restarted — first noticed in both the `stop_timeout` bug and the `container:<id>` gap
      above. Fixed in `docker/client.py`'s `recreate()`: the rollback path now also calls
      `start()` on the renamed-back container (best-effort — a failure there is logged but doesn't
      mask the original exception). Confirmed live 2026-07-15 with a clean create()-time failure
      (recreate onto a nonexistent image id) unrelated to the network-mode gap: same container id,
      same name, `status=running` afterward — a failed update no longer means unplanned downtime
      on top of the update failure itself. Re-tried against the `container:<id>` cross-run
      scenario specifically (before that gap was fixed, see above): the restart attempt itself
      failed there too, for the *same* underlying reason (the old container's own stored
      `NetworkMode` also references the dead target id) — expected, and correctly logged without
      masking the original error, not a flaw in this fix. The cascading-recreate fix above closes
      that scenario entirely now, so it no longer applies in practice.
