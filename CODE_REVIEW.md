# Code review — pre-1.0 pass

**Date:** 2026-07-15 · **Version reviewed:** 0.9.0 (`main`, 6df62b9) · **Scope:** full codebase

Baseline health is good: 108 tests pass, `ruff check` and `mypy --strict` are clean, the docs are
unusually honest about known gaps, and the live-testing checklist is at 100%. The findings below
are ordered by how likely they are to hurt a real user after 1.0.

---

## Correctness findings

### 1. HIGH — Recreate pins the *old image's* config defaults onto the new image

`build_create_kwargs()` (`lookout/docker/recreate.py:66`) copies the container's effective
`Config` verbatim: `Cmd`, `Entrypoint`, `Env`, `Labels`, `WorkingDir`, `User`, `StopSignal`,
`ExposedPorts`, and `Healthcheck`. But `docker inspect`'s `Config` is the *merge* of user-supplied
options and the old image's own defaults — e.g. `tests/fixtures/inspect/comprehensive.json` shows
the image-provided `PATH` sitting in `Config.Env`, and an un-overridden `CMD` shows up in
`Config.Cmd` exactly as if the user had typed it.

Passing all of it explicitly to `create()` means the new image's defaults never take effect:

- Image v2 renames its entrypoint script or changes its default `CMD` → the recreated container
  runs the v1 command and crash-loops. This is the core auto-update use case failing precisely
  when an image changes.
- Image v2 changes a default env var (`PATH`, `JAVA_OPTS`, an app version) → shadowed forever by
  the v1 value.
- Image v2 fixes its `HEALTHCHECK` → old one is pinned.

Watchtower handles this by subtracting the old image's config from the container config in
`GetCreateConfig` (dropping `Env`/`Labels`/`ExposedPorts` entries that match the image, and
clearing `Cmd`/`Entrypoint` when they equal the image's) so only genuinely user-supplied values
are replayed. lookout has the old image's id (`Container.image_id`) and can inspect it before
building kwargs, so the same subtraction is implementable without new data. This is the one
finding I'd consider a 1.0 blocker.

### 2. HIGH — Legacy `--link` links are used for ordering but dropped on recreate

`Container.links()` (`lookout/docker/container.py:56`) reads `HostConfig.Links` to build the
stop/start order, and `docs/linked-containers.md` advertises that legacy links are honored — but
`build_create_kwargs()` never emits a `links` kwarg. The first time a linked container is updated,
its replacement is created without the link: the `/etc/hosts` alias and injected env vars vanish
and the dependent app breaks, silently, only after an update. Either carry `HostConfig.Links` over
(docker-py's create supports `links`) or document loudly in `limitations.md` that legacy links are
ordering-only and don't survive recreate.

### 3. MEDIUM — Networks added via `docker network connect` are lost for bridge-mode containers

`_build_networks()` (`lookout/docker/recreate.py:272`) returns `[]` whenever `NetworkMode` is a
default mode (`bridge`/`default`/…). A container created on the default bridge and later attached
to a custom network with `docker network connect` has `NetworkMode: "bridge"` but two entries in
`NetworkSettings.Networks` — on recreate it comes back attached to the bridge only. Containers
*created* with `--network mynet` are fine (their `NetworkMode` is the network name). Fix: build
attachments from `NetworkSettings.Networks` whenever it lists anything beyond the default bridge,
regardless of mode.

### 4. MEDIUM — network-mode cascade + monitor-only target = sidecar recreated every poll

`_cascade_network_mode_dependents()` (`lookout/core/updater.py:143`) forces a recreate of any
container whose netns target is in `session.stale`. But `to_update` (`updater.py:71`) then drops
monitor-only containers. Scenario: `web` is stale and labeled `io.lookout.monitor-only`;
`sidecar` runs `--net=container:web`. Every poll, `web` stays stale-but-untouched, `sidecar` gets
force-cascaded (it's in `network_mode_forced`, so the same-image restart-in-place shortcut at
`updater.py:94` is skipped) and is fully stopped/recreated — a pointless restart loop of the
sidecar for as long as `web` stays stale. The same happens when a `no-pull` target takes the
restart-in-place shortcut (same image id, no actual recreate) — its dependents are still force-
recreated for nothing.

Root cause: the cascade decides "target will get a new id" before the update loop knows whether
it actually will. Since start order guarantees the target is processed before its dependent,
the dependent's iteration can instead check whether the target's name is in the set of containers
*actually recreated this run* (e.g. track recreated names alongside `session.updated`) and fall
back to the restart shortcut otherwise.

### 5. MEDIUM — Several HostConfig fields are silently dropped on recreate

Not mapped anywhere in `build_create_kwargs()`:

- `DeviceRequests` (`--gpus`) — a GPU workload comes back **without GPU access**. Worst of the
  bunch; docker-py supports `device_requests`.
- `Runtime` (`--runtime=nvidia`, gVisor, Kata) — same class of breakage.
- `VolumesFrom`, `CapDrop`-adjacent `GroupAdd` is handled but `UsernsMode`, `UTSMode`,
  `CgroupParent`, `Isolation` are not.
- `DnsSearch` / `DnsOptions` (only `Dns` is carried).
- CPU pinning/quota: `CpusetCpus`, `CpusetMems`, `CpuQuota`, `CpuPeriod`; also `BlkioWeight`,
  `OomScoreAdj`, `OomKillDisable`, `MemoryReservation`, `MemorySwappiness`.
- `--mount type=tmpfs` mounts are skipped *silently* in `_build_mounts()`
  (`recreate.py:214`) while `--tmpfs` (`HostConfig.Tmpfs`) is preserved — inconsistent, and the
  recreated app loses a writable path with no log line.

For 1.0: carry over at least `DeviceRequests`, `Runtime`, and `DnsSearch`/`DnsOptions`; for
anything still unmapped, log a WARNING when the field is non-default instead of dropping it
silently, and list the set in `docs/limitations.md`. (Per the testing conventions, new mappings
want a real captured fixture each.)

### 6. MEDIUM — Orphaned `<name>-lookout-old` containers permanently block future updates

`recreate()` (`lookout/docker/client.py:169`) renames the old container to
`{name}-lookout-old`. Two ways that stopped container can be left behind: lookout is
SIGKILLed/crashes between rename and the final `remove()`, or the final
`containers.get(...).remove()` itself fails after a successful start (that exception marks the
update failed but the new container is already running under the real name). Because
`list_containers(all=False)` never sees stopped containers, the orphan is invisible to lookout —
and the *next* update of that container fails at the rename step with a 409 name conflict, every
poll, until someone manually removes it. Suggested: on each run (or at startup), sweep stopped
`*-lookout-old` containers whose base name exists as a running container; or use a unique suffix
per attempt plus the sweep. At minimum, document the recovery step.

### 7. LOW — A daemon error during the staleness check aborts the whole run

`_is_stale()` is called outside the per-container `try` (`lookout/core/updater.py:66`), so a
transient Docker API error in `find_local_image_id()` propagates out of `run()` and skips every
remaining container (and the notification) for that pass, while an equivalent *registry* error is
caught and recorded as `(container, "check failed")`. Move the `_is_stale` call inside the `try`.

### 8. LOW — Rollback can mask the original failure and strand the temp name

In `recreate()`'s except path (`lookout/docker/client.py:202`), `self.rename(container,
container.name)` is outside any guard: if the rename-back itself fails (daemon hiccup, or the new
container still holds the name because its `remove(force=True)` failed), that second exception
replaces the original one and the old container is left named `-lookout-old` (see finding 6).
Wrap the rename-back like the restart is wrapped, and log-but-continue.

### 9. LOW — The `-P` rationale is factually wrong (behavior is still defensible)

`recreate.py:13` and `docs/limitations.md` claim there's "no way to tell in hindsight" whether a
host port came from `-P` — but `HostConfig.PublishAllPorts` records exactly that (it's `false` in
all four fixtures). lookout could honor it (`publish_all_ports=True`, skip replaying bindings for
image-exposed ports). Keeping Watchtower-parity pinning is a fine choice, but the docs should say
"we deliberately match Watchtower" rather than "it's impossible".

### 10. LOW — Cascade can't protect dependents that were filtered out

`_cascade_network_mode_dependents` only iterates `targets` (post-filter). If a sidecar is excluded
by `--include`/`--exclude`/`--label-enable` while its netns target is monitored, the target's
update leaves the sidecar's stored `container:<old-id>` reference permanently dead — exactly what
the cascade exists to prevent. User-chosen filtering, but it deserves a WARNING log when a stale
container has a filtered-out netns dependent, and a note in `docs/container-selection.md`.

---

## Loose ends to clear before 1.0

1. **No CI for tests/lint/types.** `.github/workflows/` has only docs + publish. CLAUDE.md's own
   bar is "pytest, ruff, mypy all clean" — add a workflow running all three on push/PR, ideally as
   a required check before the release tag. This is the top process gap.
2. **`cron_schedule` is dead config** (`lookout/config.py:16`): documented in `limitations.md` as
   reserved, but shipping a no-op setting at 1.0 makes it API surface you can't rename later.
   Implement it or delete the field before tagging (deleting after 1.0 is a breaking change;
   `extra="ignore"` means removal now is harmless).
3. **Reserved-but-unwired lifecycle labels** `io.lookout.lifecycle.pre-check`/`post-check`
   (`lookout/core/lifecycle.py:8-9`). Same reasoning as above — implement or drop the constants
   (docs already say they're inert, so dropping is safe now, awkward later).
4. **No `--version` flag.** The version is logged at startup, but `lookout --version` is the
   first thing someone will try when filing a bug. One line: `@click.version_option(__version__)`
   in `cli.py`.
5. **Stale doc contradicting the code:** `docs/linked-containers.md:22-24` says network-namespace
   sharing is *not* treated as an implicit link — but `Container.links()` includes
   `network_mode_target()` and commit 9a7f45b added the full cascade. Rewrite that paragraph
   (it's now a feature, and its monitor-only edge from finding 4 belongs there too).
6. **`docs/LIVE_TESTING.md` isn't in `mkdocs.yml` nav** — it gets built and published as an
   orphan page (reachable by URL, unlisted). Either add it to the nav or exclude it from the site.
7. **No tag↔version consistency guard in `publish.yml`.** Tagging `v1.0.0` with `pyproject.toml`
   still at 0.9.0 publishes an image whose `--version`/startup log contradicts its Docker tag. Add
   a step that fails unless the tag matches `project.version`.
8. **`registry_password` should be `SecretStr`** (`lookout/config.py:65`) so a future
   `Settings` repr/log line can't leak it.
9. **One `httpx.Client` per image check** (`lookout/registry/digest.py:117`): every check pays TLS
   setup twice (probe + manifest), and N images on one registry open N clients per poll. Passing a
   per-run client alongside the existing per-run `AuthCache` is a small, natural change.
10. **Notification failures in `--run-once`:** `apprise.notify()` returning falsy is handled, but
    an exception from `send()` propagates and fails the pass after updates already happened
    (in daemon mode `run_forever` catches it). Consider a catch-and-log around `send()` in
    `cli.job()`.
11. **Dockerfile niceties:** `pip install uv` is unpinned (unreproducible builds — pin a version
    or use `ghcr.io/astral-sh/uv`), and the final image runs as root (defensible given it needs
    the Docker socket; worth one sentence in the docs).
12. **If PyPI publishing is ever planned:** the `lookout` name is very likely taken; also add a
    `py.typed` marker if the package is meant to be importable. Not needed for the Docker-only
    distribution today.

---

## What looked good

- The rename-first, rollback-with-restart `recreate()` sequence and its rationale comments are
  excellent, and each "caught live" note is backed by a fixture or checklist entry.
- `_is_stale()`'s RepoDigests-orphaning fallback is subtle and correctly tested from both
  directions (`test_updater.py:225`, `:252`).
- Registry auth: per-run challenge caching, the GHCR bogus-scope workaround, and the
  scoped-fallback-credentials design (with the real regression it prevents documented) are all
  sound.
- Filter self-exemption via `/proc/self/mountinfo` instead of `$HOSTNAME` is the right call and
  well-explained.
- Test suite style matches the stated conventions: real captured fixtures for Docker shapes,
  readable Protocol fakes for orchestration.
