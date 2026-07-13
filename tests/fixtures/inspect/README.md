# Inspect fixtures

Real `docker inspect <container>` JSON output, one file per scenario, used
by `tests/test_recreate.py` to assert `recreate.build_create_kwargs`
round-trips config faithfully.

Scenarios to capture per the handoff doc's suggested implementation order:
- bind mounts
- named volumes
- custom networks
- env vars
- labels
- restart policy
- healthcheck
