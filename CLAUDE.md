# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`lookout` is a Python reimplementation of [Watchtower](https://github.com/containrrr/watchtower)'s
core functionality: poll running Docker containers, compare each one's running image against the
registry's latest digest for the same tag, and recreate containers that are stale — preserving
their runtime config (mounts, networks, restart policy, healthcheck, etc.).

The full architecture, module layout, data flow, and rationale (including *why* this is a Python
rewrite rather than a Go fix) live in `watchtower-py-handoff.md` — read it before making structural
changes. The original Go project can be cloned to `watchtower/` (gitignored) for reference if
needed; it is not part of this codebase.

v1 non-goals: Docker Swarm service updates, an HTTP API/webhook trigger, multi-host/fleet
management (single Docker daemon per instance).

## Commands

```
uv sync                        # install deps + create .venv
uv run pytest                  # full test suite
uv run pytest tests/test_recreate.py::test_comprehensive_container_healthcheck  # single test
uv run ruff check .            # lint
uv run ruff check --fix .      # lint, autofixing what's safe
uv run mypy lookout            # type check (strict mode)
uv run lookout --run-once      # one poll/update pass against the real Docker daemon
uv run lookout --help          # full CLI flag list
```

All three checks (pytest, ruff, mypy) must be clean before considering a change done.

## Architecture

Data flow for one run (`core/updater.py:run`):

```
docker_client.list_containers()
  -> core/filter.apply()          # self-exemption, name include/exclude, label-enable scope, disable-label
  -> for each: registry.get_latest_digest() vs core/updater._is_stale()
  -> core/updater.stop_order()    # dependents before dependencies
  -> lifecycle.pre_update() + docker_client.stop()      in stop_order
  -> pull/get_image_id -> docker_client.recreate() -> start() -> lifecycle.post_update()
                                                         in reversed(stop_order)
  -> optional cleanup of superseded images (best-effort; relies on Docker's own
     "image in use" rejection rather than reference-counting ourselves)
  -> Session (updated/failed/stale/skipped) -> notifications/notify.send()
```

Each module's job:

- **`docker/container.py`** — `Container` domain model wrapping a raw `docker inspect` payload plus
  a separately-populated `repo_digests` list (from the *image*, not the container, inspect —
  `DockerPyClient.list_containers()` fetches it). Label constants live here:
  `io.lookout.enable` / `.monitor-only` / `.no-pull` / `.depends-on` (deliberately **not**
  Watchtower's `com.centurylinklabs.watchtower.*` — this project doesn't aim for label
  compatibility). `has_digest()` is a *fast-path-only* check against `repo_digests` — `False` does
  **not** mean stale. Docker clears an image's `RepoDigests` whenever its tag gets locally
  reassigned to a different image (e.g. `docker build -t`/`docker pull` of the same tag by anything
  other than lookout itself), which orphans this metadata on the image a container is still
  actually running. An earlier version treated empty `repo_digests` as proof of freshness and
  silently failed to update in exactly that situation — caught live by rebuilding and repushing a
  test image on the same host running lookout. See `core/updater._is_stale()` for the real check.
- **`docker/client.py`** — `DockerClient` Protocol + `DockerPyClient`, a thin wrapper over
  `docker-py`. Deliberately lets the SDK auto-negotiate the API version (no pinned
  `DOCKER_API_VERSION`) — that was the actual bug in the Go original this project exists to avoid.
- **`docker/recreate.py`** — the riskiest module: translates a `Container`'s inspect data into
  `containers.create()` kwargs. Returns a `RecreateSpec` (create-kwargs + a `networks` list), not a
  plain dict, because multi-network containers must be connected to their non-default networks
  *after* creation — Docker rejects attaching extra networks to a container created in `"none"`
  mode, so `client.py`'s `recreate()` lets the default bridge auto-attach at create time, then
  disconnects it and connects the real target networks (this was a real bug caught during live
  testing, not a hypothetical). Known gaps, documented in the module docstring: SELinux mount
  relabeling, ulimits/sysctls/devices/dns/extra_hosts/tmpfs, and `--net=container:X` are not
  carried over.
- **`registry/digest.py`** — `parse_image()` mirrors Docker's own registry/namespace defaulting
  rules (no prefix → Docker Hub, single-segment repo → `library/`). `RegistryClient` does the
  root-probe → Bearer-challenge → token-exchange → manifest-HEAD flow, which covers Docker Hub and
  GHCR/ECR-style registries with one code path. Note: the root `/v2/` probe's challenge often
  contains a bogus placeholder `scope` (confirmed live against GHCR) — always recompute scope from
  the actual repository, never trust the probe's value.
- **`registry/auth.py`** — reads `~/.docker/config.json`'s plain `auths` section (what
  `docker login` writes). Does **not** shell out to `credHelpers`/`credsStore` binaries; returns
  `None` in that case and the caller falls back to anonymous access.
- **`core/filter.py`** — inclusion only (name include/exclude, `--label-enable` scope,
  disable-via-label). Monitor-only / no-pull are *not* filtered out here — they're read per-container
  in `updater.py` via `Container.is_monitor_only()` / `is_no_pull()` combined with the matching
  global `Settings` flag, since those containers still need to be checked and reported, just not
  updated. Also unconditionally excludes lookout's own container — not overridable by `--include`,
  since lookout stopping itself to recreate itself has no way to recover if the recreate fails
  partway through. `_detect_own_container_id()` deliberately does *not* use `$HOSTNAME` (that's
  whatever `--hostname` was set to, and stack/Compose deployments often pin one unrelated to the
  container's real id — caught live: a Portainer-deployed container kept checking itself because
  its hostname was stale from an earlier deployment). Instead it reads `/proc/self/mountinfo`'s
  `/etc/hostname` bind-mount source, which Docker always sets to
  `/var/lib/docker/containers/<real-id>/hostname` regardless of any hostname override; `$HOSTNAME`
  is only a last-resort fallback if `/proc` isn't available at all.
- **`core/updater.py`** — orchestration. `_is_stale()` is the real staleness check: it trusts
  `Container.has_digest()` when it says fresh, but when that comes back `False` it falls back to
  `DockerClient.find_local_image_id()` (looks up whatever local image currently carries the
  registry's digest, regardless of which container "owns" it) and compares that id against the
  container's own `image_id` — this is what actually catches the RepoDigests-orphaning case rather
  than assuming empty means fresh. `stop_order()` topologically sorts so dependents (via legacy
  container links or the `depends-on` label) stop before what they depend on;
  `reversed(stop_order(...))` is reused as the start order so dependencies come back up first.
- **`core/lifecycle.py`** — pre/post-update hooks via real `docker exec`, gated on the
  `io.lookout.lifecycle.pre-update` / `.post-update` labels being present.
- **`core/session.py`** — `Session.summary()` is deliberately careful to exclude containers already
  counted in `updated` or `failed` from the "Stale (not updated)" section, since `stale` accumulates
  *everything* found out of date regardless of what happened next.
- **`scheduler.py`** — sleeps in 1-second increments (not one big `time.sleep(interval)`) so
  SIGTERM/SIGINT are handled within ~1s instead of waiting out the full poll interval — this matters
  because lookout normally runs as a container itself and `docker stop` escalates to SIGKILL after a
  grace period.
- **`cli.py`** — config is env-vars-first (`LOOKOUT_*`, via `pydantic-settings` in `config.py`) with
  CLI flags as optional overrides. Every flag defaults to `None`/unset so that *not* passing a flag
  never clobbers a value already set via env var.

## Testing conventions

- **Fixture-driven, not hand-built mocks, for anything touching real Docker/registry shapes.**
  `tests/fixtures/inspect/*.json` are real `docker inspect` output (not synthesized), captured via
  live containers exercising bind mounts, named volumes, multiple custom networks with aliases,
  restart policy, and healthchecks — see `tests/fixtures/inspect/README.md`. If you extend
  `recreate.py` to handle another Docker feature, capture a new real fixture rather than
  hand-writing JSON.
- **`core/updater.py` tests use hand-rolled fakes** (`FakeDockerClient`, `FakeRegistryClient` in
  `tests/test_updater.py`), not a mocking library — they're simple enough to just implement the
  Protocol directly, which also keeps the tests readable as a spec of the orchestration contract.
- **Live verification is part of how this codebase was built, not part of CI.** Every module that
  touches the real Docker daemon or a real registry (client.py, recreate.py, digest.py, updater.py,
  the scheduler's signal handling) was manually exercised against a live daemon/Docker
  Hub/GHCR/a personal basic-auth-only registry during development, and it's how several real bugs
  were actually found — fixture/fake-based tests alone did not catch any of them: the
  network-attachment bug in `recreate.py`, the GHCR bogus-scope bug in `digest.py`, and the
  RepoDigests-orphaning bug in `core/updater._is_stale()` (only reproducible by rebuilding and
  repushing the *same tag* on the same host running lookout — see `testing/mutator/`). When
  changing those modules, prefer re-running a live check over trusting the mocked suite alone.
- **`testing/mutator/`** is a small scaffold (Dockerfile + `push.sh`/`watch.sh`/`verify.sh`) for
  exactly that kind of live check: it bakes a build-time timestamp into `/version` so every rebuild
  changes the digest under a fixed tag, letting you prove lookout actually detects and recreates
  against a real registry. Note `registry/digest.py` always probes `https://`, so this only proves
  anything end-to-end against a registry with real TLS — see the scaffold's own README.
