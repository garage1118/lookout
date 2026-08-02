# Roadmap

Planned for a future release, not yet implemented. See [Limitations](docs/limitations.md) for
everything else that is out of scope or deliberately deferred without a planned date.

- **Cron-style scheduling.** Only plain interval polling (`--interval`/`LOOKOUT_INTERVAL_SECONDS`)
  is implemented today. `scheduler.py`'s `run_forever()` is written so a cron-based scheduler can
  replace its sleep loop later without touching the update logic itself.
- **Per-registry poll interval.** Today a single `--interval`/`LOOKOUT_INTERVAL_SECONDS` value
  applies to every container's registry check, regardless of which registry it comes from. A
  separate, faster interval for one registry would let lookout notice a fresh push there sooner. A
  private registry under active development is the typical case, while other registries such as
  Docker Hub stay on the slower default. This would need per-registry scheduling state, not just a
  second interval value — `scheduler.py`'s single sleep loop assumes one cadence for every
  container today. Same underlying shape as the registry-credential fallback below — both are
  really "a table of per-registry-host overrides," just with different fields (interval vs.
  username/password). Whichever gets built first should land as one general host-keyed override
  structure with optional fields, not two separate bespoke ones.
- **`--health-check` mode**, for use as a container `HEALTHCHECK` command. Watchtower's version
  (`cmd/root.go`) is a no-op that just `os.Exit(0)`s — it proves a second process can still spawn
  in the container's namespace, nothing about whether the main loop is actually alive or stuck; not
  worth copying as-is. A real liveness check (e.g. a heartbeat file the daemon loop touches, that
  `--health-check` reads the age of) would catch a hung poll loop, but writing to disk every second
  is more machinery than justified without a concrete need. Deferred until someone actually needs
  this.
- **Registry credential helper support** (`credHelpers`/`credsStore` in `config.json`), for
  example for GCR/ECR. `registry/auth.py`'s `resolve_auth()` only reads the plain `auths` section
  today. It falls through to anonymous access if credentials live behind a helper instead. See
  [Private registries](docs/private-registries.md). Real support means lookout itself shelling out
  to a `docker-credential-<name>` binary (the same protocol Watchtower gets for free by depending
  on `docker/cli`'s `credentials.NewNativeStore`) — that binary, and whatever *it* needs to
  authenticate (AWS credentials for ECR, a GPG keyring for `pass`, etc.), would all need to live
  inside lookout's own container, not just on the host. A cheaper partial alternative: extend the
  existing `LOOKOUT_REGISTRY_HOST`/`USERNAME`/`PASSWORD` fallback (currently a single credential
  triple, scoped to one registry) into a list of triples, one per registry — the same host-keyed
  override table the per-registry poll interval item above needs, just with username/password
  fields instead of (or alongside) an interval field. That's a small, self-contained change with no
  new toolchain baked into the image, and it fully covers any
  registry whose credentials don't expire (Docker Hub, GHCR, self-hosted basic auth, GCR via a
  `_json_key` service-account key). It does not help ECR, though — the underlying problem there
  isn't where the secret is stored, it's that `aws ecr get-login-password` tokens expire every 12
  hours, so a static credential (wherever it lives) needs something to keep regenerating it
  regardless.
- **Recreate flags**: `--remove-volumes` (removing anonymous volumes on update),
  `--include-stopped`/`--include-restarting`/`--revive-stopped` (lookout only ever considers
  already-running containers today), and `--rolling-restart` (containers are always processed as a
  full stop-all/start-all batch per the dependency order, not one at a time).
- **Pre-validate exotic `NetworkMode` values** (for example `host`, or other driver-specific modes
  other than `container:<id>`) against the live daemon before recreate, instead of relying on the
  rollback-capable create-attach-start path to fail safely after the fact. This would only improve
  the error message, not change the outcome — see
  [Limitations](docs/limitations.md#container-recreation).
- **Notification message templating.** `notify.send()` always ships a fixed plain-text summary
  (`session.summary()`) to every configured Apprise URL. There is no way to customize the body or
  title format, unlike Watchtower's Go-template support. See
  [Notifications](docs/notifications.md#report-format).
- **Notification log-level filtering.** lookout has no equivalent of Watchtower's
  `--notifications-level`. It can only send the fixed per-run summary and startup message, with no
  way to route arbitrary application log lines (for example WARN and above) to a notification
  channel independent of the run summary.
