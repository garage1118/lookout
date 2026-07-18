# Roadmap

Planned for a future release — not yet implemented. See [Limitations](docs/limitations.md) for
everything else that's out of scope or deliberately deferred without a planned date.

- **Cron-style scheduling.** Only plain interval polling (`--interval`/`LOOKOUT_INTERVAL_SECONDS`)
  is implemented today. `scheduler.py`'s `run_forever()` is written so a cron-based scheduler can
  replace its sleep loop later without touching the update logic itself.
- **`--health-check` mode**, for use as a container `HEALTHCHECK` command.
- **Registry credential helper support** (`credHelpers`/`credsStore` in `config.json`), e.g. for
  GCR/ECR. `registry/auth.py`'s `resolve_auth()` only reads the plain `auths` section today and
  falls through to anonymous access if credentials live behind a helper instead — see
  [Private registries](docs/private-registries.md).
- **Recreate flags**: `--remove-volumes` (removing anonymous volumes on update),
  `--include-stopped`/`--include-restarting`/`--revive-stopped` (lookout only ever considers
  already-running containers today), and `--rolling-restart` (containers are always processed as a
  full stop-all/start-all batch per the dependency order, not one at a time).
- **Pre-validate exotic `NetworkMode` values** (e.g. `host`, or other driver-specific modes other
  than `container:<id>`) against the live daemon before recreate, instead of relying on the
  rollback-capable create-attach-start path to fail safely after the fact. Would only improve the
  error message, not change the outcome — see [Limitations](docs/limitations.md#container-recreation).
- **Notification message templating.** `notify.send()` always ships a fixed plain-text summary
  (`session.summary()`) to every configured Apprise URL — no way to customize the body/title
  format, unlike Watchtower's Go-template support. See [Notifications](docs/notifications.md#report-format).
- **Notification log-level filtering.** No equivalent of Watchtower's `--notifications-level`: lookout
  can only send the fixed per-run summary and startup message, with no way to route arbitrary
  application log lines (e.g. WARN+) to a notification channel independent of the run summary.
