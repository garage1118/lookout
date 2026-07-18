# Mutator test image

A minimal image + scripts for proving lookout actually detects and acts on a
new digest, without touching anything real. `Dockerfile` bakes a timestamp
into `/version` at build time — every rebuild changes the image's digest
under the *same tag*, which is exactly the condition lookout is watching for.

## Important: lookout requires HTTPS

`registry/digest.py` always probes the registry over `https://`. A plain
local `registry:2` container (no TLS) is enough to test `push.sh`/`watch.sh`/
`verify.sh` mechanically, but lookout's own digest check will fail against
it — point `LOOKOUT_TEST_IMAGE` at your real personal registry (which
presumably already terminates TLS) to test the actual update loop end to end.

## Usage

Set `LOOKOUT_TEST_IMAGE` once so you don't have to repeat it (defaults to
`localhost:5000/lookout-test:latest` if unset):

```bash
export LOOKOUT_TEST_IMAGE=registry.example.com/you/lookout-test:latest
```

1. **Push an initial version and start the watched container:**

   ```bash
   ./push.sh
   ./watch.sh
   ./verify.sh   # note the version, image id, and created timestamp
   ```

2. **Mutate and push again** (same tag, new digest):

   ```bash
   ./push.sh
   ```

3. **Run lookout against it**, scoped to just this container so nothing else
   on your host gets touched:

   ```bash
   uv run lookout --run-once --log-level DEBUG --include lookout-test
   ```

4. **Confirm it actually recreated the container**, not just left it alone:

   ```bash
   ./verify.sh
   ```

   The version should match what you just pushed, and both the container id
   and created timestamp should have changed from step 1 — a changed
   `/version` alone isn't proof; if the id/timestamp *didn't* change, lookout
   didn't actually recreate anything.

Repeat steps 2–4 to test the loop as many times as you like. `watch.sh` is
there to reset back to a clean starting container if you want to re-run from
scratch.

## Things worth testing beyond the basic loop

- **`--label-enable`, `--exclude`**: add `--label io.lookout.enable=false` (or
  omit `--include`, add `--exclude lookout-test`) and confirm `push.sh` +
  `--run-once` leaves the container alone.
- **`io.lookout.monitor-only=true`**: confirm lookout logs it as stale but
  does not recreate it.
- **`io.lookout.lifecycle.pre-update`/`.post-update`**: add labels running a
  command that writes to a file, then check it landed after the run.
- **`--cleanup`**: confirm the old image gets removed after a successful
  update once nothing else references it.
- **The containerized path**: once the local-loop logic is proven, do one
  pass with lookout itself running as a container (`-v
  /var/run/docker.sock:/var/run/docker.sock`, plus a `config.json` mount if
  your registry needs auth) rather than `uv run` — see
  `../../docs/private-registries.md`.
