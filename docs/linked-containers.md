# Linked containers

If containers depend on each other, lookout stops and starts them in an order that keeps the
dependency working: **lookout stops dependents before what they depend on, and starts them
after**. This way, nothing briefly runs against an already-torn-down dependency.

lookout reads dependencies from two places, and honors both:

- **Legacy Docker links** (`HostConfig.Links`, i.e. the old `--link` flag).
- The **`io.lookout.depends-on`** label, a comma-separated list of container names:

```bash
docker run -d --name wordpress --label io.lookout.depends-on=mysql wordpress-image
```

If `mysql` needs an update, lookout stops `wordpress` first, then `mysql`. On the way back up, it
recreates and starts `mysql` first, then `wordpress`.

lookout also replays a legacy `--link` onto the recreated container itself, not just uses it for
ordering, so the link's `/etc/hosts` alias and injected environment variables still work after an
update. The `depends-on` label is ordering-only — it does not create a Docker link. It exists
specifically for the modern, link-free way of connecting containers: a shared user-defined
network, where name-based DNS resolution needs no explicit link at all.

lookout builds the dependency graph only from containers that are actually stale in the current
run. It does not stop a healthy dependency just because something depends on it.

lookout treats a container that shares another container's network namespace
(`--net=container:<name>`) as an implicit dependent of that container too, at the same tier as an
explicit `depends-on` label. lookout stops it first and starts it last. If its target is
recreated this run, lookout forces it to recreate alongside the target, even if its own image has
not changed. This matters because Docker resolves a `container:<name>` reference to a concrete id
at create time and never updates it again — a dependent left behind after its target is recreated
would permanently reference a dead container.

This cascade does not apply to a monitor-only target, since lookout can never actually recreate it
while monitor-only holds. There would be nothing to protect the dependent against.
