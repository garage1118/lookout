# Inspect fixtures

Real `docker inspect <container>` JSON output, one file per scenario, used
by `tests/test_recreate.py` to assert `recreate.build_create_kwargs`
round-trips config faithfully. Per CLAUDE.md's testing conventions: if you
extend `recreate.py` to handle another Docker feature, capture a new real
fixture here rather than hand-writing JSON.

- `minimal.json` — a container with no extras: no mounts, networks,
  healthcheck, restart policy, or custom hostname.
- `comprehensive.json` — bind mount, named volume, multiple custom networks
  with aliases, env vars, labels, restart policy, healthcheck, capabilities,
  ports.
- `selinux-relabel.json` — captured live on an SELinux-enforcing RHEL 9 host
  (Docker CE, `selinux-enabled: true`): a `:z`-relabeled bind, a
  `:Z`-relabeled bind, a plain unlabeled bind, and a `:z`-relabeled named
  volume, all on one container. Confirmed live that the recreated container
  preserves correct SELinux read/write behavior for each.
