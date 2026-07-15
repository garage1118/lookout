# Linked containers

If containers depend on each other, lookout stops and starts them in an order that keeps the
dependency working: **dependents are stopped before what they depend on, and started after** —
so nothing is briefly running against an already-torn-down dependency.

Dependencies are read from two places, and both are honored:

- **Legacy Docker links** (`HostConfig.Links`, i.e. the old `--link` flag).
- The **`io.lookout.depends-on`** label, a comma-separated list of container names:

```bash
docker run -d --name wordpress --label io.lookout.depends-on=mysql wordpress-image
```

If `mysql` needs an update, lookout will stop `wordpress` first, then `mysql`; on the way back up
it recreates and starts `mysql` first, then `wordpress`.

A legacy `--link` is also replayed onto the recreated container itself (not just used for
ordering), so the link's `/etc/hosts` alias and injected environment variables still work after an
update. The `depends-on` label is ordering-only — it doesn't create a Docker link, since it exists
specifically for the modern, link-free way of connecting containers (a shared user-defined network,
where name-based DNS resolution needs no explicit link at all).

The dependency graph is only built from containers that are actually stale in the current run —
lookout doesn't stop a healthy dependency just because something depends on it.

A container sharing another container's network namespace (`--net=container:<name>`) is treated as
an implicit dependent of that container too, the same tier as an explicit `depends-on` label — it's
stopped first and started last, and if its target is recreated this run, it's forced to recreate
alongside it even if its own image hasn't changed (Docker resolves a `container:<name>` reference
to a concrete id at create time and never updates it again, so a dependent left behind after its
target is recreated would permanently reference a dead container). This cascade doesn't apply to a
monitor-only target, since it can never actually be recreated by lookout while monitor-only holds —
there'd be nothing to protect the dependent against.
